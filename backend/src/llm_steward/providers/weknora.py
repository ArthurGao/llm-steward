"""WeKnora KnowledgeProvider (design §8), for the pinned v0.9.0 (deploy/weknora).

Mapping, checked against the running v0.9.0 server (2026-11-09):
- a collection is a WeKnora knowledge base named `steward-<collection_id>` (its title in the
  description), on the embedding model `STEWARD_WEKNORA_EMBEDDING_MODEL_ID` (registered by
  deploy/weknora/bootstrap.py);
- an entry is one "manual" knowledge item: created with `status: publish` (a draft is never
  indexed), replaced by its WeKnora id (`existing_id`), tagged with `custom_metadata
  {steward_id, version}`; its WeKnora id is the external id;
- WeKnora parses, embeds and deletes in the background: an upsert waits until the item is
  searchable (`finalizing` / `completed`) and a remove until it is gone, so the contract holds
  (searchable after upsert, no hit after remove). Each operation has ONE deadline (`budget_s`,
  under the worker's per-job limit): a new item that fails (to parse, or to be tagged) is deleted
  again so a retry starts clean; an upsert still parsing at the deadline is returned (it becomes
  searchable shortly, and a retry would only duplicate it); a remove not finished raises, and its
  retry is safe (404 = gone). A Syncer-level pending → confirm step, so no adapter blocks the
  worker, is a follow-up (it changes the sync state machine);
- a document is one "file" knowledge item (`POST .../knowledge/file`, multipart), parsed in the
  background with no publish step; the Syncer polls `document_status`. Uploading the same bytes
  into the same knowledge base again is a 409 `duplicate_file` whose body carries the existing item:
  its id is returned, so a retried upload never makes a second copy. A failed item does not count
  as a duplicate (WeKnora skips it), so a new upload after a failure starts afresh;
- search (`/knowledge-search`, several knowledge bases at once) returns chunks: each entry's (or
  document's) best chunk is kept. There is no metadata filter, so the governance layer filters.

Errors carry the HTTP status and WeKnora's error code, never a response body (it may echo text).
"""

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

import httpx2

from llm_steward.errors import ProviderUnavailable
from llm_steward.models.enums import DocStatus
from llm_steward.providers.base import (
    DOC_PARSING,
    ENTRY_UPSERT,
    ComponentHealth,
    KbCollectionRef,
    KbDocumentUpload,
    KbEntryDoc,
    ProviderHit,
    group_hits,
)

log = logging.getLogger(__name__)

API = "/api/v1"
SEARCHABLE = frozenset({"finalizing", "completed"})  # finalizing: vectors are in, extras run on
# A file item's parse_status (v0.9.0 internal/types/knowledge.go) as a document status; anything
# else is still in progress (and the Syncer gives up after its deadline).
DOC_STATUSES = {
    "pending": DocStatus.PENDING,
    "processing": DocStatus.PROCESSING,
    **dict.fromkeys(SEARCHABLE, DocStatus.READY),  # vectors are in, as for entries
    "failed": DocStatus.FAILED,
    "cancelled": DocStatus.FAILED,
    "deleting": DocStatus.FAILED,
}


class WeKnoraProvider:
    name = "weknora"
    capabilities = frozenset({ENTRY_UPSERT, DOC_PARSING})

    def __init__(
        self,
        http: httpx2.AsyncClient,
        base_url: str,
        api_key: str | None,
        embedding_model_id: str | None,
        *,
        timeout_s: float,
        budget_s: float = 20.0,
        first_poll_s: float = 0.2,
    ) -> None:
        """`timeout_s`: health and search (retrieval's budget). `budget_s`: everything one upsert
        or remove may take, waits included — under the worker's per-job limit (half the lease)."""
        self._http = http
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = embedding_model_id
        self._timeout_s = timeout_s
        self._budget_s = budget_s
        self._first_poll_s = first_poll_s

    async def health(self) -> ComponentHealth:
        try:
            response = await self._http.get(f"{self._base_url}/health", timeout=self._timeout_s)
        except httpx2.TimeoutException:
            return ComponentHealth(self.name, "error", f"timeout after {self._timeout_s:g}s")
        except httpx2.HTTPError as exc:
            return ComponentHealth.from_exception(self.name, exc)
        if response.status_code != 200:
            return ComponentHealth(self.name, "error", f"HTTP {response.status_code}")
        return ComponentHealth(self.name, "ok")

    async def _call(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        timeout_s: float,
        missing_ok: bool = False,
        existing_ok: bool = False,
        files: dict[str, tuple[str, bytes, str]] | None = None,
        form: dict[str, str] | None = None,
    ) -> Any:
        """The `data` of a successful call; None for a 404 when `missing_ok`; with `existing_ok`,
        the item a 409 carries (an upload WeKnora already holds) counts as the result."""
        what = f"weknora {method} {path}"
        try:
            response = await self._http.request(
                method,
                f"{self._base_url}{API}{path}",
                json=body,
                files=files,
                data=form,
                headers={"X-API-Key": self._api_key or ""},
                timeout=max(timeout_s, 0.1),
            )
        except httpx2.HTTPError as exc:
            raise ProviderUnavailable(f"{what}: {type(exc).__name__}") from exc
        if response.status_code == 404 and missing_ok:
            return None
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if response.is_success and payload.get("success", True):
            return payload.get("data")
        existing = payload.get("data")
        if existing_ok and response.status_code == 409 and isinstance(existing, dict):
            if not existing.get("id"):
                raise ProviderUnavailable(f"{what}: HTTP 409 without the existing item")
            return existing
        error = payload.get("error")
        if isinstance(error, dict):  # handler errors: a numeric code (the message may echo input)
            detail = f"error code {error.get('code')}"
        elif isinstance(error, str):  # auth/RBAC middleware: a short fixed message
            detail = error.split(":")[0][:60]
        else:
            detail = ""
        status = "" if response.is_success else f"HTTP {response.status_code} "
        raise ProviderUnavailable(f"{what}: {status}{detail}".rstrip())

    async def ensure_collection(self, collection: KbCollectionRef) -> str:
        """Find the collection's knowledge base by name, or create it: a crash between creating it
        and recording its id must not leave a second one."""
        if not self._model:
            raise ProviderUnavailable(
                "weknora needs an embedding model for new collections: set "
                "STEWARD_WEKNORA_EMBEDDING_MODEL_ID (deploy/weknora/bootstrap.py prints it)"
            )
        name = f"steward-{collection.collection_id}"
        timeout = self._budget_s
        existing = await self._call("GET", "/knowledge-bases", timeout_s=timeout)
        for kb in existing or []:
            if kb.get("name") == name:
                return str(kb["id"])
        body = {"name": name, "description": collection.name, "embedding_model_id": self._model}
        data = await self._call("POST", "/knowledge-bases", body, timeout_s=timeout)
        return str(data["id"])

    async def upsert_entry(
        self, entry: KbEntryDoc, collection_ext_id: str, existing_id: str | None = None
    ) -> str:
        deadline = time.monotonic() + self._budget_s
        body = {"title": entry.title, "content": entry.content, "status": "publish"}
        replaced = existing_id is not None and (
            await self._call(  # replace in place; gone from WeKnora → create it again
                "PUT",
                f"/knowledge/manual/{existing_id}",
                body,
                timeout_s=_left(deadline),
                missing_ok=True,
            )
            is not None
        )
        if replaced:
            assert existing_id is not None
            knowledge_id = existing_id
        else:
            path = f"/knowledge-bases/{collection_ext_id}/knowledge/manual"
            data = await self._call("POST", path, body, timeout_s=_left(deadline))
            knowledge_id = str(data["id"])
        try:
            metadata = {"steward_id": str(entry.entry_id), "version": entry.version}
            await self._call(
                "PUT",
                f"/knowledge/{knowledge_id}",
                {"custom_metadata": metadata},  # create ignores it; the title stays as written
                timeout_s=_left(deadline),
            )
            data = await self._wait(knowledge_id, deadline, _parsed)
            if data is not None and data.get("parse_status") == "failed":
                # WeKnora's error_message can echo the content: the status says enough
                raise ProviderUnavailable(f"weknora could not parse {knowledge_id}")
        except ProviderUnavailable:
            if not replaced:  # a half-made new item: remove it so a retry starts clean
                await self._call(
                    "DELETE",
                    f"/knowledge/{knowledge_id}",
                    timeout_s=self._budget_s,
                    missing_ok=True,
                )
            raise
        return knowledge_id  # still parsing at the deadline: searchable shortly (see module doc)

    async def remove(self, external_id: str) -> None:
        """Delete and wait until it is gone: the Syncer records the entry as removed when this
        returns. 404: already gone (so a retry after a timeout is safe)."""
        deadline = time.monotonic() + self._budget_s
        queued = await self._call(
            "DELETE", f"/knowledge/{external_id}", timeout_s=_left(deadline), missing_ok=True
        )
        if queued is None:
            return
        if await self._wait(external_id, deadline, lambda data: data is None) is not None:
            raise ProviderUnavailable(
                f"weknora {external_id} not deleted within {self._budget_s:g}s"
            )

    async def ingest_document(self, doc: KbDocumentUpload, collection_ext_id: str) -> str:
        """Upload the file, then tag it (`custom_metadata`; the upload's own `metadata` field is
        not searchable). Parsing runs in the background: the Syncer polls `document_status`."""
        deadline = time.monotonic() + self._budget_s
        data = await self._call(
            "POST",
            f"/knowledge-bases/{collection_ext_id}/knowledge/file",
            files={"file": (doc.filename, doc.data, doc.mime or "application/octet-stream")},
            form={"fileName": doc.filename},
            timeout_s=_left(deadline),
            existing_ok=True,
        )
        knowledge_id = str(data["id"])
        metadata = {"steward_id": str(doc.document_id), "kind": "document"}
        await self._call(
            "PUT",
            f"/knowledge/{knowledge_id}",
            {"custom_metadata": metadata},
            timeout_s=_left(deadline),
        )
        return knowledge_id

    async def document_status(self, external_id: str) -> DocStatus:
        """Gone from WeKnora (404) is final: FAILED. The item's error_message is never read (it
        can echo the document)."""
        data = await self._call(
            "GET", f"/knowledge/{external_id}", timeout_s=self._budget_s, missing_ok=True
        )
        if data is None:
            return DocStatus.FAILED
        return DOC_STATUSES.get(str(data.get("parse_status")), DocStatus.PROCESSING)

    async def _wait(self, knowledge_id: str, deadline: float, done: Callable[[Any], bool]) -> Any:
        """GET the item (None when gone) until `done(item)` or the deadline; the last item seen.
        Polls back off (doubling up to 2 s): most parses take one to three seconds."""
        delay = self._first_poll_s
        while True:
            data = await self._call(
                "GET", f"/knowledge/{knowledge_id}", timeout_s=_left(deadline), missing_ok=True
            )
            if done(data) or time.monotonic() + delay >= deadline:
                if not done(data):
                    log.warning("weknora %s not settled within %gs", knowledge_id, self._budget_s)
                return data
            await asyncio.sleep(delay)
            delay = min(delay * 2, 2.0)

    async def search(
        self,
        query: str,
        collection_ext_ids: list[str],
        *,
        top_k: int,
        filters: dict[str, Any] | None = None,
    ) -> list[ProviderHit]:
        if not collection_ext_ids:
            return []
        body = {
            "query": query,
            "knowledge_base_ids": collection_ext_ids,
            "match_count": min(top_k, 200),  # chunks: grouped per item below
        }
        chunks = await self._call("POST", "/knowledge-search", body, timeout_s=self._timeout_s)
        hits = (
            ProviderHit(str(c["knowledge_id"]), float(c["score"]), c.get("content", ""))
            for c in chunks or []
        )
        return group_hits(hits, top_k)


def _left(deadline: float) -> float:
    return deadline - time.monotonic()


def _parsed(data: Any) -> bool:
    """Settled for an upsert: searchable, failed, or (oddly) gone."""
    return data is None or data.get("parse_status") in SEARCHABLE | {"failed"}
