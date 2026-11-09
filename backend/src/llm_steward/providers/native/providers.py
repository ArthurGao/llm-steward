"""Native MemoryProvider and KnowledgeProvider: contract-test reference and single-machine mode.

Search is hybrid inside the provider: vector KNN and full text, fused by reciprocal rank.
"""

import asyncio
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

from llm_steward.errors import Unprocessable
from llm_steward.ids import uuid7
from llm_steward.models.enums import DocStatus
from llm_steward.providers.base import (
    DELETE,
    DOC_PARSING,
    ENTRY_UPSERT,
    METADATA_FILTER,
    UPDATE,
    ComponentHealth,
    KbCollectionRef,
    KbDocumentUpload,
    KbEntryDoc,
    MemoryEvent,
    MemoryRecord,
    Message,
    ProviderHit,
    Scope,
    group_hits,
)
from llm_steward.providers.native.embedding import Embedder
from llm_steward.providers.native.store import NativeItem, NativeStore, StoredItem
from llm_steward.providers.native.text import tokens
from llm_steward.ranking import rrf_scores

# A full-text hit joins the fusion only if it contains at least this share of the query's distinct
# tokens. Without it, one generic word ("user", "用户") outranked the vector's clear winner.
MIN_LEXICAL_COVERAGE = 0.5
# Each retriever contributes this many candidates per requested result before fusion.
CANDIDATES_PER_RESULT = 3

MEMORY = "memory"
KB = "kb"
# A document is its chunks (kind KB, searchable, ids `doc:<id>#<n>`) plus one manifest row (kind
# and partition DOC, which no search reads) that records how many chunks there are (0: failed).
DOC = "kb_doc"
DOC_PREFIX = "doc:"
TEXT_TYPES = frozenset({".txt", ".md", ".markdown"})  # what the reference implementation parses
MAX_CHUNK_CHARS = 1000


def create_store(engine: AsyncEngine, embedder: Embedder, schema: str) -> NativeStore:
    if engine.dialect.name == "postgresql":
        from llm_steward.providers.native.store_pg import PgNativeStore

        return PgNativeStore(engine, embedder.model, embedder.dim, schema)
    from llm_steward.providers.native.store_sqlite import SqliteNativeStore

    return SqliteNativeStore(engine, embedder.model, embedder.dim, schema)


def lexical_matches(query_tokens: list[str], hits: list[StoredItem]) -> list[StoredItem]:
    """Keep full-text hits that cover enough of the query to count as a real lexical match.
    Uses the tokens stored at write time, so no text is re-segmented on the request path."""
    wanted = set(query_tokens)
    if not wanted:
        return []
    return [h for h in hits if len(wanted & set(h.tokens)) / len(wanted) >= MIN_LEXICAL_COVERAGE]


def rrf(rankings: list[list[StoredItem]], top_k: int) -> list[ProviderHit]:
    scores = rrf_scores([[i.external_id for i in ranking] for ranking in rankings])
    texts = {i.external_id: i.text for ranking in rankings for i in ranking}
    best = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:top_k]
    return [ProviderHit(eid, score, texts[eid]) for eid, score in best]


def _memory_part(namespace_id: object) -> str:
    return f"{MEMORY}:{namespace_id}"


def _kb_part(collection_ext_id: str) -> str:
    return f"{KB}:{collection_ext_id}"


def _scope_of(item: StoredItem) -> Scope:
    return Scope(
        uuid.UUID(item.metadata["namespace_id"]), item.user_id, item.agent_id, item.session_id
    )


def _scope_filter(scope: Scope) -> dict[str, str]:
    values = {"user_id": scope.user_id, "agent_id": scope.agent_id, "session_id": scope.session_id}
    return {k: v for k, v in values.items() if v is not None}


class _NativeBase:
    name = "native"

    def __init__(self, store: NativeStore, embedder: Embedder) -> None:
        self._store = store
        self._embedder = embedder

    async def health(self) -> ComponentHealth:
        """Ready means the index exists for this model and layout; a failure here is what every
        search and sync would hit, so /health reports it instead of claiming ok."""
        await self._store.ready()
        return ComponentHealth(self.name, "ok", "builtin")

    async def _hybrid(
        self, parts: list[str], query: str, top_k: int, scope: dict[str, str]
    ) -> list[ProviderHit]:
        """Embed and segment the query once; search every partition both ways, concurrently."""
        limit = top_k * CANDIDATES_PER_RESULT
        [embedding] = await self._embedder.embed([query])
        query_tokens = tokens(query)

        async def one(part: str) -> list[ProviderHit]:
            by_vector, by_text = await asyncio.gather(
                self._store.vector_search(part, embedding, limit, scope),
                self._store.text_search(part, query_tokens, limit, scope),
            )
            return rrf([by_vector, lexical_matches(query_tokens, by_text)], top_k)

        per_part = await asyncio.gather(*(one(p) for p in parts))
        hits = [h for part_hits in per_part for h in part_hits]
        return sorted(hits, key=lambda h: h.score, reverse=True)[:top_k]

    async def _write(self, item: NativeItem) -> bool:
        return (await self._write_all([item]))[0]

    async def _write_all(self, items: list[NativeItem]) -> list[bool]:
        """Embedded in one call (the embedder batches), then stored one by one."""
        embeddings = await self._embedder.embed([i.text for i in items]) if items else []
        return [
            await self._store.upsert(item, embedding, tokens(item.text))
            for item, embedding in zip(items, embeddings, strict=True)
        ]


class NativeMemoryProvider(_NativeBase):
    capabilities = frozenset({UPDATE, DELETE, METADATA_FILTER})

    async def add(
        self,
        messages: list[Message],
        scope: Scope,
        *,
        infer: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> list[MemoryEvent]:
        if infer:
            raise Unprocessable(
                "the native memory provider cannot extract memories (no 'extract' capability); "
                "send infer=false"
            )
        metadata = {**(metadata or {}), "namespace_id": str(scope.namespace_id)}
        external_id = str(metadata.get("steward_id") or uuid7())
        content = "\n".join(m.content for m in messages)
        existed = await self._write(
            NativeItem(
                part=_memory_part(scope.namespace_id),
                kind=MEMORY,
                external_id=external_id,
                text=content,
                user_id=scope.user_id,
                agent_id=scope.agent_id,
                session_id=scope.session_id,
                metadata=metadata,
            )
        )
        return [MemoryEvent("UPDATE" if existed else "ADD", external_id, content)]

    async def search(
        self, query: str, scope: Scope, *, top_k: int, filters: dict[str, Any] | None = None
    ) -> list[ProviderHit]:
        return await self._hybrid(
            [_memory_part(scope.namespace_id)], query, top_k, _scope_filter(scope)
        )

    async def get(self, external_id: str) -> MemoryRecord | None:
        item = await self._store.get(MEMORY, external_id)
        if item is None:
            return None
        return MemoryRecord(item.external_id, item.text, _scope_of(item), item.metadata)

    async def update(self, external_id: str, content: str) -> None:
        item = await self._store.get(MEMORY, external_id)
        if item is None:
            raise Unprocessable(f"no native memory {external_id!r} to update")
        await self._write(
            NativeItem(
                part=_memory_part(_scope_of(item).namespace_id),
                kind=MEMORY,
                external_id=external_id,
                text=content,
                user_id=item.user_id,
                agent_id=item.agent_id,
                session_id=item.session_id,
                metadata=item.metadata,
            )
        )

    async def delete(self, external_id: str) -> None:
        await self._store.delete(MEMORY, external_id)

    async def list(self, scope: Scope) -> list[MemoryRecord]:
        items = await self._store.list_items(_memory_part(scope.namespace_id), _scope_filter(scope))
        return [MemoryRecord(i.external_id, i.text, _scope_of(i), i.metadata) for i in items]


def _chunk_id(document_ext_id: str, n: int) -> str:
    return f"{document_ext_id}#{n}"


def _answering_id(hit_id: str) -> str:
    """The id a hit answers for: a document chunk's document (entry ids carry no '#')."""
    return hit_id.split("#", 1)[0]


def _parse(doc: KbDocumentUpload) -> list[str]:
    """The chunks of a plain-text or Markdown file; [] for anything else or not UTF-8."""
    if not any(doc.filename.lower().endswith(t) for t in TEXT_TYPES):
        return []
    try:
        return chunk_text(doc.data.decode("utf-8"))
    except UnicodeDecodeError:
        return []


def chunk_text(text: str) -> list[str]:
    """Paragraphs (blank-line separated), long ones cut at MAX_CHUNK_CHARS."""
    paragraphs = [" ".join(p.split()) for p in text.split("\n\n")]
    return [
        p[i : i + MAX_CHUNK_CHARS]
        for p in paragraphs
        if p
        for i in range(0, len(p), MAX_CHUNK_CHARS)
    ]


class NativeKnowledgeProvider(_NativeBase):
    capabilities = frozenset({ENTRY_UPSERT, METADATA_FILTER, DOC_PARSING})

    async def ensure_collection(self, collection: KbCollectionRef) -> str:
        # Collections need no storage of their own: the id is the partition key.
        return f"{collection.namespace_id}:{collection.collection_id}"

    async def upsert_entry(
        self, entry: KbEntryDoc, collection_ext_id: str, existing_id: str | None = None
    ) -> str:
        external_id = str(entry.entry_id)
        await self._write(
            NativeItem(
                part=_kb_part(collection_ext_id),
                kind=KB,
                external_id=external_id,
                text=f"{entry.title}\n{entry.content}",
                metadata={"version": entry.version, "tags": entry.tags},
            )
        )
        return external_id

    async def ingest_document(self, doc: KbDocumentUpload, collection_ext_id: str) -> str:
        """Parsed at once (plain text and Markdown); anything else is recorded as failed."""
        external_id = f"{DOC_PREFIX}{doc.document_id}"
        await self.remove(external_id)  # a re-upload replaces every chunk
        chunks = await asyncio.to_thread(_parse, doc)  # a large file: off the event loop
        items = [
            NativeItem(
                part=_kb_part(collection_ext_id),
                kind=KB,
                external_id=_chunk_id(external_id, n),
                text=chunk,
                metadata={"document": external_id},
            )
            for n, chunk in enumerate(chunks)
        ]
        manifest = NativeItem(
            part=DOC,
            kind=DOC,
            external_id=external_id,
            text=doc.filename,
            metadata={"chunks": len(chunks)},
        )
        await self._write_all([*items, manifest])
        return external_id

    async def document_status(self, external_id: str) -> DocStatus:
        manifest = await self._store.get(DOC, external_id)
        if manifest is None or not manifest.metadata.get("chunks"):
            return DocStatus.FAILED  # gone, or nothing it could read
        return DocStatus.READY

    async def remove(self, external_id: str) -> None:
        if not external_id.startswith(DOC_PREFIX):
            await self._store.delete(KB, external_id)
            return
        manifest = await self._store.get(DOC, external_id)
        for n in range(manifest.metadata.get("chunks", 0) if manifest else 0):
            await self._store.delete(KB, _chunk_id(external_id, n))
        await self._store.delete(DOC, external_id)

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
        parts = [_kb_part(c) for c in collection_ext_ids]
        hits = await self._hybrid(parts, query, top_k, {})
        # a document's chunks answer for the document
        answering = (ProviderHit(_answering_id(h.external_id), h.score, h.text) for h in hits)
        return group_hits(answering, top_k)
