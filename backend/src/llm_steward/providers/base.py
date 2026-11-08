"""Provider contracts (design §8).

Adapters are reached only through these Protocols; the governance layer reads `capabilities` and
degrades accordingly instead of branching on a provider's name.
"""

import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from llm_steward.errors import ProviderUnavailable

ComponentStatus = Literal["ok", "error", "not_implemented"]

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
    text: str


def best_hit_per_id(hits: Iterable[ProviderHit], top_k: int) -> list[ProviderHit]:
    """One hit per external id (its best: engines return chunks), best first, ties by id."""
    best: dict[str, ProviderHit] = {}
    for hit in hits:
        if hit.external_id not in best or hit.score > best[hit.external_id].score:
            best[hit.external_id] = hit
    return sorted(best.values(), key=lambda h: (-h.score, h.external_id))[:top_k]


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
    ) -> list[ProviderHit]: ...


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


class _Pending:
    """A configured provider whose adapter belongs to a later stage: health says so, calls 503."""

    capabilities: frozenset[str] = frozenset()

    def __init__(self, name: str, stage: str) -> None:
        self.name = name
        self.stage = stage

    def _unavailable(self) -> ProviderUnavailable:
        return ProviderUnavailable(f"the {self.name} adapter arrives in {self.stage}")

    async def health(self) -> ComponentHealth:
        return ComponentHealth(self.name, "not_implemented", f"adapter arrives in {self.stage}")


class PendingMemoryProvider(_Pending):
    async def add(
        self,
        messages: list[Message],
        scope: Scope,
        *,
        infer: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> list[MemoryEvent]:
        raise self._unavailable()

    async def search(
        self, query: str, scope: Scope, *, top_k: int, filters: dict[str, Any] | None = None
    ) -> list[ProviderHit]:
        raise self._unavailable()

    async def get(self, external_id: str) -> MemoryRecord | None:
        raise self._unavailable()

    async def update(self, external_id: str, content: str) -> None:
        raise self._unavailable()

    async def delete(self, external_id: str) -> None:
        raise self._unavailable()

    async def list(self, scope: Scope) -> list[MemoryRecord]:
        raise self._unavailable()


class PendingKnowledgeProvider(_Pending):
    async def ensure_collection(self, collection: KbCollectionRef) -> str:
        raise self._unavailable()

    async def upsert_entry(
        self, entry: KbEntryDoc, collection_ext_id: str, existing_id: str | None = None
    ) -> str:
        raise self._unavailable()

    async def remove(self, external_id: str) -> None:
        raise self._unavailable()

    async def search(
        self,
        query: str,
        collection_ext_ids: list[str],
        *,
        top_k: int,
        filters: dict[str, Any] | None = None,
    ) -> list[ProviderHit]:
        raise self._unavailable()
