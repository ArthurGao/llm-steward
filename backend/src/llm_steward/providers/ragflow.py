"""RAGFlow KnowledgeProvider (design §8), for the pinned v1.0.0 (deploy/ragflow).

Mapping, checked against the running v1.0.0 server (2026-11-11; facts in KB global/ragflow):
- a collection is a dataset named `steward-<collection_id>` (v1.0.0 takes no description), on
  the tenant's default embedding model (set by deploy/ragflow/bootstrap.py). It is found by that
  exact name first: RAGFlow would not refuse a second one, it would call it `…(1)`;
- an entry is one document `entry-<entry_id>.txt` holding ONE chunk added by hand (title and
  content), which is searchable at once — no parsing. The external id is `<dataset>/<document>`
  (every document call needs both). An update patches that chunk; a document gone from RAGFlow is
  uploaded again; a document already there under the entry's name (uploaded, then the worker died
  before recording it) is reused, so a retry never makes a second copy;
- a document is a file uploaded as `<document_id>-<filename>`, then parsed in RAGFlow's own time
  (`POST /datasets/{id}/chunks`); the Syncer polls `document_status`. RAGFlow keeps a second copy of
  the same bytes, so the name is what finds the copy it already holds;
- search is `/api/v1/retrieval` over several datasets, with no engine floor
  (`similarity_threshold` 0: governance ranks and decides, as with native and lite-rag) and the
  vector side leading (`keywords_similarity_weight` 0.3: at the default 0.7 a clear paraphrase
  scored 0.31). Chunks are grouped per document.

v1.0.0 answers most failures with HTTP 200 and a non-zero `code`; a missing document is code 102,
not 404, so "gone" is decided by asking for the document by id. Errors carry the HTTP status and
RAGFlow's code, never its message (it can echo what it was sent).
"""

import time
from typing import Any

import httpx2

from llm_steward.errors import ProviderUnavailable
from llm_steward.models.enums import DocStatus
from llm_steward.providers.base import (
    DOC_PARSING,
    ENTRY_UPSERT,
    WRITE_BUDGET_S,
    ComponentHealth,
    KbCollectionRef,
    KbDocumentUpload,
    KbEntryDoc,
    ProviderHit,
    collection_name,
    group_hits,
)

API = "/api/v1"
KEYWORDS_WEIGHT = 0.3
MAX_PAGE = 100  # RAGFlow rejects larger pages
# A document's `ingestion_status` (v1.0.0 internal/common/constants.go, dao/document.go); anything
# not listed (UNSTART, CREATED, SCHEDULED) has not started yet.
DOC_STATUSES = {
    "RUNNING": DocStatus.PROCESSING,
    "STOPPING": DocStatus.PROCESSING,
    "COMPLETED": DocStatus.READY,
    "FAILED": DocStatus.FAILED,
    "STOPPED": DocStatus.FAILED,
}
NOT_STARTED = "UNSTART"


class RAGFlowProvider:
    name = "ragflow"
    capabilities = frozenset({ENTRY_UPSERT, DOC_PARSING})

    def __init__(
        self,
        http: httpx2.AsyncClient,
        base_url: str,
        api_key: str | None,
        *,
        timeout_s: float,
        budget_s: float = WRITE_BUDGET_S,
        parser_id: str = "general",
    ) -> None:
        """`timeout_s`: health and search (retrieval's budget). `budget_s`: everything one write
        may take. `parser_id`: the built-in parser new datasets use (`general`, `paper`, `table`,
        `laws`, `book`, …: RAGFlow's reason to be the complex-documents engine)."""
        self._parser_id = parser_id
        self._http = http
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout_s = timeout_s
        self._budget_s = budget_s

    async def health(self) -> ComponentHealth:
        """`/system/healthz` checks RAGFlow's own database, doc engine, storage and queue."""
        try:
            response = await self._http.get(
                f"{self._base_url}{API}/system/healthz", timeout=self._timeout_s
            )
            status = response.json().get("status") if response.status_code == 200 else None
        except httpx2.TimeoutException:
            return ComponentHealth(self.name, "error", f"timeout after {self._timeout_s:g}s")
        except (httpx2.HTTPError, ValueError) as exc:
            return ComponentHealth.from_exception(self.name, exc)
        if status != "ok":
            return ComponentHealth(self.name, "error", f"HTTP {response.status_code}, {status}")
        return ComponentHealth(self.name, "ok")

    async def _call(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        deadline: float,
        params: dict[str, str] | None = None,
        files: dict[str, tuple[str, bytes, str]] | None = None,
        accept: frozenset[int] = frozenset(),
    ) -> tuple[int, Any]:
        """(code, data). Code 0 is success; a code in `accept` is returned for the caller to
        read; anything else raises."""
        what = f"ragflow {method} {path.split('/')[1]}"
        try:
            response = await self._http.request(
                method,
                f"{self._base_url}{API}{path}",
                json=body,
                params=params,
                files=files,
                headers={"Authorization": f"Bearer {self._api_key or ''}"},
                timeout=max(deadline - time.monotonic(), 0.1),
            )
        except httpx2.HTTPError as exc:
            raise ProviderUnavailable(f"{what}: {type(exc).__name__}") from exc
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        result = payload.get("code") if isinstance(payload, dict) else None
        if response.is_success and (result == 0 or result in accept):
            return int(result), payload.get("data")
        status = "" if response.is_success else f"HTTP {response.status_code} "
        raise ProviderUnavailable(f"{what}: {status}code {result}".rstrip())

    def _deadline(self) -> float:
        return time.monotonic() + self._budget_s

    # --- collections ---------------------------------------------------------------------------

    async def ensure_collection(self, collection: KbCollectionRef) -> str:
        """Find the collection's dataset by its exact name, or create it: a crash between creating
        it and recording its id must not leave a second one."""
        deadline = self._deadline()
        name = collection_name(collection.collection_id)
        _, found = await self._call("GET", "/datasets", params={"name": name}, deadline=deadline)
        for dataset in found or []:
            if dataset.get("name") == name:
                return str(dataset["id"])
        # v1.0.0 refuses fields it does not list ("extra inputs are not permitted"), so the
        # collection's title cannot ride along; the name carries the collection id
        body = {"name": name, "parse_type": 1, "parser_id": self._parser_id}  # 1: built-in
        _, created = await self._call("POST", "/datasets", body, deadline=deadline)
        return str(created["id"])

    # --- entries -------------------------------------------------------------------------------

    async def upsert_entry(
        self, entry: KbEntryDoc, collection_ext_id: str, existing_id: str | None = None
    ) -> str:
        deadline = self._deadline()
        text = f"{entry.title}\n{entry.content}"
        name = f"entry-{entry.entry_id}.txt"
        document = None
        if existing_id is not None:
            dataset, doc = _split(existing_id)
            if dataset == collection_ext_id and await self._docs(dataset, {"id": doc}, deadline):
                document = doc
        if document is None:  # never synced, gone, or synced but not recorded (a crash)
            held = await self._named(collection_ext_id, name, deadline)
            document = str(held["id"]) if held is not None else None
        chunks = f"/datasets/{collection_ext_id}/documents/{{}}/chunks"
        if document is None:  # a new document has no chunk: add its one chunk straight away
            document = await self._upload(
                collection_ext_id, name, text.encode(), "text/plain", deadline
            )
            await self._call("POST", chunks.format(document), {"content": text}, deadline=deadline)
        else:
            await self._replace_chunk(chunks.format(document), text, deadline)
        return f"{collection_ext_id}/{document}"

    async def _replace_chunk(self, path: str, text: str, deadline: float) -> None:
        """The entry's one chunk, patched in place (or added, if its document has none)."""
        _, listed = await self._call("GET", path, deadline=deadline)
        found = (listed or {}).get("chunks") or []
        body = {"content": text}
        if found:
            await self._call("PATCH", f"{path}/{found[0]['id']}", body, deadline=deadline)
        else:
            await self._call("POST", path, body, deadline=deadline)

    async def remove(self, external_id: str) -> None:
        """Delete the document; it leaves retrieval at once. Already gone is fine (a retry)."""
        dataset, document = _split(external_id)
        deadline = self._deadline()
        path = f"/datasets/{dataset}/documents"
        result, _ = await self._call(
            "DELETE", path, {"ids": [document]}, deadline=deadline, accept=frozenset({102})
        )
        if result != 0 and await self._docs(dataset, {"id": document}, deadline):
            raise ProviderUnavailable(f"ragflow DELETE datasets: code {result}")

    # --- documents -----------------------------------------------------------------------------

    async def ingest_document(self, doc: KbDocumentUpload, collection_ext_id: str) -> str:
        """Upload the file and start parsing it; the Syncer polls `document_status`. The copy
        RAGFlow already holds under this document's name is returned instead (started, if a
        crash came between the upload and the start)."""
        deadline = self._deadline()
        name = f"{doc.document_id}-{doc.filename}"
        held = await self._named(collection_ext_id, name, deadline)
        if held is None:
            mime = doc.mime or "application/octet-stream"
            document = await self._upload(collection_ext_id, name, doc.data, mime, deadline)
        elif held.get("ingestion_status", NOT_STARTED) == NOT_STARTED:
            document = str(held["id"])
        else:
            return f"{collection_ext_id}/{held['id']}"
        await self._call(
            "POST",
            f"/datasets/{collection_ext_id}/chunks",
            {"document_ids": [document]},
            deadline=deadline,
        )
        return f"{collection_ext_id}/{document}"

    async def document_status(self, external_id: str) -> DocStatus:
        """Gone from RAGFlow is final: FAILED. Its progress message is never read (it can echo
        the document)."""
        dataset, document = _split(external_id)
        found = await self._docs(dataset, {"id": document}, self._deadline())
        if not found:
            return DocStatus.FAILED
        return DOC_STATUSES.get(str(found[0].get("ingestion_status")), DocStatus.PENDING)

    # --- search --------------------------------------------------------------------------------

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
            "question": query,
            "dataset_ids": collection_ext_ids,
            "page_size": min(top_k, MAX_PAGE),  # chunks: grouped per document below
            "similarity_threshold": 0.0,
            "keywords_similarity_weight": KEYWORDS_WEIGHT,
        }
        _, data = await self._call(
            "POST", "/retrieval", body, deadline=time.monotonic() + self._timeout_s
        )
        hits = (
            ProviderHit(
                f"{c['dataset_id']}/{c['document_id']}",
                float(c["similarity"]),
                c.get("content", ""),
            )
            for c in (data or {}).get("chunks") or []
        )
        return group_hits(hits, top_k)

    # --- helpers -------------------------------------------------------------------------------

    async def _docs(self, dataset: str, params: dict[str, str], deadline: float) -> list[Any]:
        """The documents matching `params` (`id` or `name`); none when RAGFlow has none — v1.0.0
        answers an unknown id or name with code 102, not an empty list."""
        result, data = await self._call(
            "GET",
            f"/datasets/{dataset}/documents",
            params=params,
            deadline=deadline,
            accept=frozenset({102}),
        )
        return ((data or {}).get("docs") or []) if result == 0 else []

    async def _named(self, dataset: str, name: str, deadline: float) -> Any:
        """The document stored under exactly this name, if any."""
        docs = await self._docs(dataset, {"name": name}, deadline)
        return next((d for d in docs if d.get("name") == name), None)

    async def _upload(
        self, dataset: str, name: str, data: bytes, mime: str, deadline: float
    ) -> str:
        _, uploaded = await self._call(
            "POST",
            f"/datasets/{dataset}/documents",
            files={"file": (name, data, mime)},
            deadline=deadline,
        )
        return str(uploaded[0]["id"])


def _split(external_id: str) -> tuple[str, str]:
    dataset, sep, document = external_id.partition("/")
    if not (sep and dataset and document):
        raise ProviderUnavailable(f"{external_id!r} is not a ragflow id (<dataset>/<document>)")
    return dataset, document
