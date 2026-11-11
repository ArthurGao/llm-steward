"""Provider contracts (design §8).

Adapters are reached only through these Protocols; the governance layer reads `capabilities` and
degrades accordingly instead of branching on a provider's name.
"""

import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, cast

from llm_steward.models.enums import DocStatus

ComponentStatus = Literal["ok", "error"]

# Memory capabilities
EXTRACT = "extract"
# The engine itself resolves a new fact against existing ones on add (UPDATE / DELETE events);
# without it, llm-steward runs conflict compensation (design §5 step 4).
RESOLVES_CONFLICTS = "resolves_conflicts"
UPDATE = "update"
DELETE = "delete"
METADATA_FILTER = "metadata_filter"
# Knowledge capabilities
ENTRY_UPSERT = "entry_upsert"
DOC_PARSING = "doc_parsing"


@dataclass(frozen=True)
class ComponentHealth:
    name: str
    status: ComponentStatus
    detail: str | None = None

    @classmethod
    def from_exception(cls, name: str, exc: BaseException) -> "ComponentHealth":
        return cls(name, "error", f"{type(exc).__name__}: {exc}")


@dataclass(frozen=True)
class Scope:
    """Who a memory belongs to. `None` on a dimension means shared within the namespace."""

    namespace_id: uuid.UUID
    user_id: str | None = None
    agent_id: str | None = None
    session_id: str | None = None


@dataclass(frozen=True)
class Message:
    role: str
    content: str


@dataclass(frozen=True)
class MemoryEvent:
    op: Literal["ADD", "UPDATE", "DELETE"]
    external_id: str
    content: str | None = None
    produced_by: str | None = None  # `role:model` when an LLM produced it (extraction)


@dataclass(frozen=True)
class MemoryRecord:
    external_id: str
    content: str
    scope: Scope
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderHit:
    """One search result. `score` is provider-internal and not comparable across providers."""

    external_id: str
    score: float
    text: str  # the best chunk
    # a KB hit's best chunks, best first (`text` among them): a long document answers with
    # several passages; empty when the engine returned one
    passages: tuple[str, ...] = ()


# What one adapter write (upsert, remove, upload) may take, waits included: under the worker's
# per-job limit, which is half the job lease (services/worker.py, JobQueue.lease 60 s).
WRITE_BUDGET_S = 20.0


def collection_name(collection_id: uuid.UUID | str) -> str:
    """What an engine calls a llm-steward collection (its knowledge base or dataset): found by
    this exact name, so a crash between creating it and recording its id never makes two."""
    return f"steward-{collection_id}"


CHUNKS_PER_ID = 3  # a KB hit carries up to this many chunks of its entry or document


def group_hits(
    hits: Iterable[ProviderHit], top_k: int, per_id: int = CHUNKS_PER_ID
) -> list[ProviderHit]:
    """Engines return chunks: one hit per external id (the best `top_k` ids, by their best
    chunk, ties by id), carrying its best `per_id` chunks as `passages`."""
    by_id: dict[str, list[ProviderHit]] = {}
    for hit in hits:
        by_id.setdefault(hit.external_id, []).append(hit)
    groups = [sorted(g, key=lambda h: -h.score)[:per_id] for g in by_id.values()]
    groups.sort(key=lambda g: (-g[0].score, g[0].external_id))
    return [
        ProviderHit(g[0].external_id, g[0].score, g[0].text, tuple(h.text for h in g))
        for g in groups[:top_k]
    ]


@dataclass(frozen=True)
class KbCollectionRef:
    collection_id: uuid.UUID
    namespace_id: uuid.UUID
    name: str


@dataclass(frozen=True)
class KbEntryDoc:
    """One governed KB entry, written to the KB provider as one small document."""

    entry_id: uuid.UUID
    namespace_id: uuid.UUID
    title: str
    content: str
    version: int
    tags: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class KbDocumentUpload:
    """One governed document, uploaded whole: the provider parses and chunks it."""

    document_id: uuid.UUID
    namespace_id: uuid.UUID
    title: str
    filename: str
    mime: str | None
    data: bytes = field(repr=False)


class HealthProbe(Protocol):
    @property
    def name(self) -> str: ...

    async def health(self) -> ComponentHealth: ...


class MemoryProvider(HealthProbe, Protocol):
    @property
    def capabilities(self) -> frozenset[str]: ...

    async def add(
        self,
        messages: list[Message],
        scope: Scope,
        *,
        infer: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> list[MemoryEvent]: ...

    async def search(
        self, query: str, scope: Scope, *, top_k: int, filters: dict[str, Any] | None = None
    ) -> list[ProviderHit]: ...

    async def get(self, external_id: str) -> MemoryRecord | None: ...

    async def update(self, external_id: str, content: str) -> None: ...

    async def delete(self, external_id: str) -> None: ...

    async def list(self, scope: Scope) -> list[MemoryRecord]: ...


class KnowledgeProvider(HealthProbe, Protocol):
    @property
    def capabilities(self) -> frozenset[str]: ...

    async def ensure_collection(self, collection: KbCollectionRef) -> str: ...

    async def upsert_entry(
        self, entry: KbEntryDoc, collection_ext_id: str, existing_id: str | None = None
    ) -> str:
        """Write the entry; returns its external id. `existing_id`: the id it was last synced
        under, for engines that replace by their own id (WeKnora); others may ignore it."""
        ...

    async def remove(self, external_id: str) -> None: ...

    async def search(
        self,
        query: str,
        collection_ext_ids: list[str],
        *,
        top_k: int,
        filters: dict[str, Any] | None = None,
    ) -> list[ProviderHit]:
        """One hit per external id, at most `top_k`, from the engine's best `top_k` chunks; each
        carries up to `CHUNKS_PER_ID` of its chunks as `passages` (`group_hits`). An entry's
        extra passages go unused (governance serves its stored text): the engine cannot tell
        entries from documents, and over-fetching is the caller's (retrieval asks for more)."""
        ...


class Reranker(Protocol):
    """A cross-encoder (design §6 step 5): one relevance in [0, 1] per (query, text), same order,
    higher is better (a local model's logits go through a sigmoid in its adapter; a hosted API
    that already returns probabilities passes them on)."""

    @property
    def model(self) -> str: ...

    async def score(self, query: str, texts: list[str]) -> list[float]: ...


class DocumentParser(Protocol):
    """What a KnowledgeProvider with `DOC_PARSING` adds (design §8): documents are parsed in the
    engine's own time, so an upload returns at once and the Syncer polls the status."""

    async def ingest_document(self, doc: KbDocumentUpload, collection_ext_id: str) -> str:
        """Hand the file over; returns its external id. Uploading the same bytes again returns
        the id of the copy the engine already holds (no second copy)."""
        ...

    async def document_status(self, external_id: str) -> DocStatus:
        """READY once searchable; FAILED is final (including: the engine no longer has it)."""
        ...


def document_parser(provider: KnowledgeProvider) -> DocumentParser | None:
    """The provider's document side, or None when it cannot parse documents."""
    return cast(DocumentParser, provider) if DOC_PARSING in provider.capabilities else None


@dataclass(frozen=True)
class Providers:
    memory: MemoryProvider
    kb: KnowledgeProvider

    def by_role(self) -> dict[str, HealthProbe]:
        return {"memory_provider": self.memory, "kb_provider": self.kb}

    async def aclose(self) -> None:
        """Release what providers hold (an in-process engine's connections). `aclose` is
        optional: most providers hold nothing of their own."""
        for provider in (self.memory, self.kb):
            close = getattr(provider, "aclose", None)
            if close is not None:
                await close()
