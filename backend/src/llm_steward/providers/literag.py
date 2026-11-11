"""lite-rag KnowledgeProvider (design §8): the owner's own RAG engine, over HTTP (default: lite-rag
runs as its own service) or in-process (extra `literag`).

Mapping, defined only here:
- a llm-steward namespace is the lite-rag namespace `{prefix}/{namespace_id}`;
- a collection is the kb `c-{collection_id}` (collection names are free text, kb names are not);
  its external id is `{namespace_id}/{collection_id}`;
- an entry is the document `doc_id = entry_id`; its external id is `{collection_ext_id}#{entry_id}`,
  because `remove` is given no collection.

A search is one lite-rag multi-kb search per lite-rag namespace (`search_kbs` / `POST /v1/search`,
lite-rag S10): the query is embedded once and the collections are fused and reranked as one, then
each entry keeps its best chunk. A collection with nothing synced yet (no kb) is skipped by
lite-rag. Filters go through as lite-rag `where` (scalar values only)."""

import asyncio
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol
from urllib.parse import quote

import httpx2

from llm_steward.config import LiteRagSearchMode
from llm_steward.errors import ProviderUnavailable, Unprocessable
from llm_steward.providers.base import (
    ENTRY_UPSERT,
    METADATA_FILTER,
    ComponentHealth,
    KbCollectionRef,
    KbEntryDoc,
    ProviderHit,
    group_hits,
)

if TYPE_CHECKING:
    from lite_rag.engine import RagEngine, RagRuntime

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class LiteHit:
    kb: str
    doc_id: str
    score: float
    text: str


class Transport(Protocol):
    """lite-rag's own operations, in its own terms (namespace, kb, doc_id)."""

    async def upsert(
        self, namespace: str, kb: str, doc_id: str, title: str, text: str, metadata: dict[str, Any]
    ) -> None: ...

    async def delete(self, namespace: str, kb: str, doc_id: str) -> None:
        """Idempotent: a document (or kb) that does not exist is not an error."""
        ...

    async def search(
        self, namespace: str, kbs: list[str], query: str, top_k: int, where: dict[str, Any] | None
    ) -> list[LiteHit]:
        """The kbs searched as one; a kb that does not exist (nothing synced yet) is skipped."""
        ...

    async def health(self) -> None:
        """Raises `ProviderUnavailable` when lite-rag cannot serve."""
        ...

    async def aclose(self) -> None: ...


class LiteRagProvider:
    name = "literag"
    capabilities = frozenset({ENTRY_UPSERT, METADATA_FILTER})

    def __init__(self, transport: Transport, namespace_prefix: str = "steward") -> None:
        self._transport = transport
        self._prefix = namespace_prefix

    async def ensure_collection(self, collection: KbCollectionRef) -> str:
        # lite-rag creates the kb on its first document: nothing to do but name it.
        return f"{collection.namespace_id}/{collection.collection_id}"

    def _locate(self, collection_ext_id: str) -> tuple[str, str]:
        """(lite-rag namespace, kb) of a collection external id."""
        try:  # two parts, both uuids: anything else is ValueError
            namespace_id, collection_id = (uuid.UUID(p) for p in collection_ext_id.split("/"))
        except ValueError as exc:  # ids come from our own external_refs: a broken invariant
            raise ValueError(f"not a lite-rag collection id: {collection_ext_id!r}") from exc
        return f"{self._prefix}/{namespace_id}", f"c-{collection_id}"

    async def upsert_entry(
        self, entry: KbEntryDoc, collection_ext_id: str, existing_id: str | None = None
    ) -> str:
        namespace, kb = self._locate(collection_ext_id)
        doc_id = str(entry.entry_id)
        metadata = {"steward_id": doc_id, "version": entry.version, "tags": list(entry.tags)}
        await self._transport.upsert(namespace, kb, doc_id, entry.title, entry.content, metadata)
        return f"{collection_ext_id}#{doc_id}"

    async def remove(self, external_id: str) -> None:
        collection_ext_id, _, doc_id = external_id.rpartition("#")
        namespace, kb = self._locate(collection_ext_id)
        await self._transport.delete(namespace, kb, doc_id)

    async def search(
        self,
        query: str,
        collection_ext_ids: list[str],
        *,
        top_k: int,
        filters: dict[str, Any] | None = None,
    ) -> list[ProviderHit]:
        # one search per lite-rag namespace (a llm-steward namespace: in practice exactly one)
        by_namespace: dict[str, dict[str, str]] = {}  # namespace -> kb -> collection_ext_id
        for collection_ext_id in collection_ext_ids:
            namespace, kb = self._locate(collection_ext_id)
            by_namespace.setdefault(namespace, {})[kb] = collection_ext_id
        results = await asyncio.gather(
            *(
                self._transport.search(ns, list(kbs), query, top_k, filters)
                for ns, kbs in by_namespace.items()
            )
        )
        hits = (
            ProviderHit(f"{by_namespace[ns][h.kb]}#{h.doc_id}", h.score, h.text)
            for ns, found in zip(by_namespace, results, strict=True)
            for h in found
        )
        return group_hits(hits, top_k)

    async def health(self) -> ComponentHealth:
        try:
            await self._transport.health()
        except ProviderUnavailable as exc:
            return ComponentHealth(self.name, "error", exc.detail)
        return ComponentHealth(self.name, "ok")

    async def aclose(self) -> None:
        await self._transport.aclose()


# --- transports ------------------------------------------------------------------------------


class HttpTransport:
    """lite-rag's HTTP API (`literag serve`), with an API key whose namespaces cover the prefix.
    `mode` and `rerank` go with every search: llm-steward's choice, not lite-rag's default."""

    def __init__(
        self,
        http: httpx2.AsyncClient,
        base_url: str,
        api_key: str | None,
        timeout_s: float,
        *,
        mode: LiteRagSearchMode = "hybrid",
        rerank: bool = False,
    ) -> None:
        self._http = http  # shared, owned by the container
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout_s = timeout_s
        self._mode = mode
        self._rerank = rerank

    async def _call(
        self,
        method: str,
        path: str,
        namespace: str | None,
        body: dict[str, Any] | None = None,
        *,
        ok: tuple[int, ...] = (200,),
    ) -> httpx2.Response:
        """The response when its status is in `ok`. Otherwise a lite-rag error: the title goes
        up, the detail (lite-rag's own words) to the log; a refused request (400) is the caller's
        to fix, anything else lite-rag cannot serve."""
        headers = {"X-LiteRag-Namespace": namespace} if namespace else {}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        what = f"lite-rag {method} {path}"
        try:
            response = await self._http.request(
                method,
                f"{self._base_url}{path}",
                json=body,
                headers=headers,
                timeout=self._timeout_s,
            )
        except httpx2.HTTPError as exc:
            raise ProviderUnavailable(f"{what}: {type(exc).__name__}") from exc
        if response.status_code in ok:
            return response
        try:
            problem = response.json()
        except ValueError:
            problem = {}
        log.warning("%s: %s %s", what, response.status_code, problem)
        message = f"{what}: HTTP {response.status_code} {problem.get('title', '')}".rstrip()
        if response.status_code == 400:
            raise Unprocessable(f"{message}: {problem.get('detail', '')}".rstrip(": "))
        raise ProviderUnavailable(message)

    @staticmethod
    def _doc_path(kb: str, doc_id: str) -> str:
        return f"/v1/kbs/{kb}/documents/{quote(doc_id, safe='')}"

    async def upsert(
        self, namespace: str, kb: str, doc_id: str, title: str, text: str, metadata: dict[str, Any]
    ) -> None:
        body = {"title": title, "text": text, "metadata": metadata}
        await self._call("PUT", self._doc_path(kb, doc_id), namespace, body)

    async def delete(self, namespace: str, kb: str, doc_id: str) -> None:
        # 404: the document (or its kb) is already gone
        await self._call("DELETE", self._doc_path(kb, doc_id), namespace, ok=(204, 404))

    async def search(
        self, namespace: str, kbs: list[str], query: str, top_k: int, where: dict[str, Any] | None
    ) -> list[LiteHit]:
        body = {
            "kbs": kbs,
            "query": query,
            "top_k": top_k,
            "where": where,
            "mode": self._mode,
            "rerank": self._rerank,
        }
        response = await self._call("POST", "/v1/search", namespace, body)
        hits = response.json()["hits"]
        return [LiteHit(h["kb"], h["doc_id"], h["score"], h["text"]) for h in hits]

    async def health(self) -> None:
        await self._call("GET", "/health", None)

    async def aclose(self) -> None:
        pass  # the HTTP client belongs to the container


class InProcessTransport:
    """lite-rag as a library in this process (extra `literag`). Its calls block, so they run in
    worker threads — at most `concurrency` at once (the runtime's store pool is sized to match,
    and the default thread pool is shared with the native embedder and mem0). One engine per
    namespace, kept: an engine remembers which kbs it has checked. `mode` and `rerank` go with every
    search, as over HTTP."""

    def __init__(
        self,
        runtime: "RagRuntime",
        concurrency: int,
        *,
        mode: LiteRagSearchMode = "hybrid",
        rerank: bool = False,
    ) -> None:
        self._runtime = runtime
        self._mode = mode
        self._rerank = rerank
        self._engines: dict[str, RagEngine] = {}  # one per namespace in use
        self._slots = asyncio.Semaphore(concurrency)

    def _engine(self, namespace: str) -> "RagEngine":
        if namespace not in self._engines:
            self._engines[namespace] = self._runtime.engine(namespace)
        return self._engines[namespace]

    async def _run(self, what: str, call: Callable[[], Any], *, missing: Any = None) -> Any:
        """`call` in a worker thread. A missing kb returns `missing` (when given); lite-rag's
        other errors become ours."""
        from lite_rag.errors import InvalidRequest, KbNotFound, LiteRagError, redact

        try:
            async with self._slots:
                return await asyncio.to_thread(call)
        except KbNotFound:
            if missing is None:
                raise
            return missing
        except InvalidRequest as exc:
            raise Unprocessable(f"lite-rag {what}: {exc}") from exc
        except LiteRagError as exc:
            # a store error can carry a database URL: never log credentials
            log.warning("lite-rag %s: %s: %s", what, type(exc).__name__, redact(str(exc)))
            raise ProviderUnavailable(f"lite-rag {what}: {type(exc).__name__}") from exc

    async def upsert(
        self, namespace: str, kb: str, doc_id: str, title: str, text: str, metadata: dict[str, Any]
    ) -> None:
        engine = self._engine(namespace)
        await self._run("upsert", lambda: engine.upsert_text(kb, doc_id, title, text, metadata))

    async def delete(self, namespace: str, kb: str, doc_id: str) -> None:
        engine = self._engine(namespace)
        await self._run("delete", lambda: engine.delete(kb, doc_id))  # False when gone: fine

    async def search(
        self, namespace: str, kbs: list[str], query: str, top_k: int, where: dict[str, Any] | None
    ) -> list[LiteHit]:
        engine = self._engine(namespace)
        hits = await self._run(  # lite-rag skips a kb with nothing synced yet
            "search",
            lambda: engine.search_kbs(
                kbs, query, top_k=top_k, where=where, mode=self._mode, rerank=self._rerank
            ),
            missing=[],
        )
        return [LiteHit(h.kb, h.doc_id, h.score, h.text) for h in hits]

    async def health(self) -> None:
        await self._run("health", self._runtime.health)

    async def aclose(self) -> None:
        await asyncio.to_thread(self._runtime.close)
