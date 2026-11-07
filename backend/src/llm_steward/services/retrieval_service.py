"""Joint retrieval across the memory and KB providers (design §6, the P1 subset).

Each provider is searched in parallel under its own timeout. Hits are mapped back to governed
objects through `external_refs`; anything unknown, no longer effective, outside its validity window
or outside the caller's scope is dropped. That is the safety net: even while sync lags behind,
deprecated knowledge is never served. Results are fused by reciprocal rank. Trust weights, decay,
MMR, pinned memories and token budgets arrive in P4.
"""

import asyncio
import dataclasses
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from jinja2 import Environment, PackageLoader
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from llm_steward.errors import ProviderUnavailable
from llm_steward.ids import uuid7
from llm_steward.models import ExternalRef, KbCollection, KbEntry, Memory, RetrievalLog
from llm_steward.models.base import utcnow
from llm_steward.models.enums import (
    EFFECTIVE_KB_STATUSES,
    KbStatus,
    MemoryStatus,
    ObjectType,
    SyncStatus,
)
from llm_steward.providers.base import ProviderHit, Providers, Scope
from llm_steward.ranking import rrf_scores

log = logging.getLogger(__name__)

# Ask each provider for this many candidates per requested result: the safety net may drop some.
CANDIDATES_PER_RESULT = 3
MIN_CANDIDATES = 10
Source = Literal["memory", "kb"]

_templates = Environment(
    loader=PackageLoader("llm_steward", "prompts"),
    autoescape=False,  # plain-text prompt context, not HTML
    trim_blocks=True,
    lstrip_blocks=True,
)


@dataclass(frozen=True)
class RetrieveRequest:
    query: str
    user_id: str | None = None
    agent_id: str | None = None
    session_id: str | None = None
    sources: tuple[Source, ...] = ("memory", "kb")
    collections: tuple[str, ...] | None = None  # collection names; None = all
    top_k: int = 8
    with_context: bool = False


@dataclass(frozen=True)
class RetrievedItem:
    source: Source
    id: uuid.UUID
    content: str
    title: str | None
    status: str
    verified: bool
    score: float = 0.0


@dataclass(frozen=True)
class RetrieveResult:
    retrieval_id: uuid.UUID
    items: list[RetrievedItem]
    degraded: list[Source] = field(default_factory=list)
    context: str | None = None


class RetrievalService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        providers: Providers,
        *,
        timeout_s: float,
    ) -> None:
        self._sessions = sessions
        self._providers = providers
        self._timeout_s = timeout_s

    async def retrieve(self, namespace_id: uuid.UUID, req: RetrieveRequest) -> RetrieveResult:
        scope = Scope(namespace_id, req.user_id, req.agent_id, req.session_id)
        candidates = max(req.top_k * CANDIDATES_PER_RESULT, MIN_CANDIDATES)
        collection_ext_ids = []
        if "kb" in req.sources:
            collection_ext_ids = await self._collection_ext_ids(namespace_id, req.collections)
        searches: dict[Source, Any] = {}
        if "memory" in req.sources:
            searches["memory"] = self._providers.memory.search(req.query, scope, top_k=candidates)
        if "kb" in req.sources and collection_ext_ids:
            searches["kb"] = self._providers.kb.search(
                req.query, collection_ext_ids, top_k=candidates
            )

        outcomes = await asyncio.gather(*(self._timed(c) for c in searches.values()))
        hits: dict[Source, list[ProviderHit]] = {}
        latency_ms: dict[str, int] = {}
        degraded: list[Source] = []
        for source, (result, elapsed_ms) in zip(searches, outcomes, strict=True):
            latency_ms[source] = elapsed_ms
            if isinstance(result, BaseException):
                log.warning("retrieve: %s provider failed: %s", source, type(result).__name__)
                degraded.append(source)
            else:
                hits[source] = result
        if searches and not hits:
            raise ProviderUnavailable(f"every source failed: {', '.join(degraded)}")

        async with self._sessions() as s, s.begin():
            rankings = await self._resolve(s, namespace_id, scope, hits)
            fused = _fuse(rankings, req.top_k)
            retrieval_id = self._log(s, namespace_id, req, fused, latency_ms)
        context = _render(fused) if req.with_context else None
        return RetrieveResult(retrieval_id, fused, degraded, context)

    async def _timed(self, search: Any) -> tuple[Any, int]:
        started = time.perf_counter()
        try:
            async with asyncio.timeout(self._timeout_s):
                result = await search
        except Exception as exc:
            result = exc
        return result, int((time.perf_counter() - started) * 1000)

    async def _collection_ext_ids(
        self, namespace_id: uuid.UUID, names: tuple[str, ...] | None
    ) -> list[str]:
        query = (
            select(ExternalRef.external_id)
            .join(KbCollection, KbCollection.id == ExternalRef.object_id)
            .where(
                ExternalRef.object_type == ObjectType.KB_COLLECTION,
                ExternalRef.provider == self._providers.kb.name,
                ExternalRef.sync_status == SyncStatus.SYNCED,
                KbCollection.namespace_id == namespace_id,
            )
        )
        if names is not None:
            query = query.where(KbCollection.name.in_(names))
        async with self._sessions() as s:
            return [eid for eid in await s.scalars(query) if eid]

    async def _resolve(
        self,
        s: AsyncSession,
        namespace_id: uuid.UUID,
        scope: Scope,
        hits: dict[Source, list[ProviderHit]],
    ) -> list[list[RetrievedItem]]:
        """Map provider hits to governed objects, keeping each provider's order; drop whatever
        governance would not serve."""
        now = utcnow()
        rankings: list[list[RetrievedItem]] = []
        if hits.get("memory"):
            known = await self._memories(s, namespace_id, scope, hits["memory"], now)
            rankings.append(
                [known[h.external_id] for h in hits["memory"] if h.external_id in known]
            )
        if hits.get("kb"):
            known = await self._entries(s, namespace_id, hits["kb"], now)
            rankings.append([known[h.external_id] for h in hits["kb"] if h.external_id in known])
        dropped = sum(len(h) for h in hits.values()) - sum(len(r) for r in rankings)
        if dropped:
            log.warning("retrieve: safety net dropped %d provider hit(s)", dropped)
        return rankings

    async def _memories(
        self,
        s: AsyncSession,
        namespace_id: uuid.UUID,
        scope: Scope,
        hits: list[ProviderHit],
        now: datetime,
    ) -> dict[str, RetrievedItem]:
        query = (
            select(ExternalRef.external_id, Memory)
            .join(Memory, Memory.id == ExternalRef.object_id)
            .where(
                ExternalRef.object_type == ObjectType.MEMORY,
                ExternalRef.provider == self._providers.memory.name,
                ExternalRef.namespace_id == namespace_id,
                ExternalRef.external_id.in_([h.external_id for h in hits]),
                Memory.namespace_id == namespace_id,
                Memory.status == MemoryStatus.ACTIVE,
                Memory.valid_from <= now,
                or_(Memory.valid_to.is_(None), Memory.valid_to > now),
                or_(Memory.expires_at.is_(None), Memory.expires_at > now),
            )
        )
        for column, value in (
            (Memory.user_id, scope.user_id),
            (Memory.agent_id, scope.agent_id),
            (Memory.session_id, scope.session_id),
        ):
            # own scope or shared; a caller with no user_id only sees shared memories
            visible = or_(column.is_(None), column == value) if value else column.is_(None)
            query = query.where(visible)
        rows = await s.execute(query)
        return {
            eid: RetrievedItem("memory", m.id, m.content, None, m.status.value, False)
            for eid, m in rows
            if eid
        }

    async def _entries(
        self, s: AsyncSession, namespace_id: uuid.UUID, hits: list[ProviderHit], now: datetime
    ) -> dict[str, RetrievedItem]:
        query = (
            select(ExternalRef.external_id, KbEntry)
            .join(KbEntry, KbEntry.id == ExternalRef.object_id)
            .where(
                ExternalRef.object_type == ObjectType.KB_ENTRY,
                ExternalRef.provider == self._providers.kb.name,
                ExternalRef.namespace_id == namespace_id,
                ExternalRef.external_id.in_([h.external_id for h in hits]),
                KbEntry.namespace_id == namespace_id,
                KbEntry.status.in_(EFFECTIVE_KB_STATUSES),
                KbEntry.valid_from <= now,
                or_(KbEntry.valid_to.is_(None), KbEntry.valid_to > now),
            )
        )
        rows = await s.execute(query)
        return {
            eid: RetrievedItem(
                "kb", e.id, e.content, e.title, e.status.value, e.status == KbStatus.VERIFIED
            )
            for eid, e in rows
            if eid
        }

    @staticmethod
    def _log(
        s: AsyncSession,
        namespace_id: uuid.UUID,
        req: RetrieveRequest,
        items: list[RetrievedItem],
        latency_ms: dict[str, int],
    ) -> uuid.UUID:
        entry = RetrievalLog(
            id=uuid7(),  # set now: the id is returned before the transaction flushes
            namespace_id=namespace_id,
            query=req.query,
            filters={
                "user_id": req.user_id,
                "agent_id": req.agent_id,
                "session_id": req.session_id,
                "sources": list(req.sources),
                "collections": list(req.collections) if req.collections else None,
                "top_k": req.top_k,
            },
            results=[{"source": i.source, "id": str(i.id), "score": i.score} for i in items],
            latency_ms=latency_ms,
        )
        s.add(entry)
        return entry.id


def _fuse(rankings: list[list[RetrievedItem]], top_k: int) -> list[RetrievedItem]:
    """Reciprocal rank fusion: provider scores are not comparable across engines, ranks are."""
    scores = rrf_scores([[item.id for item in ranking] for ranking in rankings])
    items = {item.id: item for ranking in rankings for item in ranking}
    best = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:top_k]
    return [dataclasses.replace(items[item_id], score=score) for item_id, score in best]


def _render(items: list[RetrievedItem]) -> str:
    return (
        _templates.get_template("context_render.j2")
        .render(
            memories=[i for i in items if i.source == "memory"],
            kb=[i for i in items if i.source == "kb"],
        )
        .strip()
    )
