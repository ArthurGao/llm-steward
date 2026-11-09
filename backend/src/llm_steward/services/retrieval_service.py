"""Joint retrieval across the memory and KB providers (design §6, the P1 subset).

Each provider is searched in parallel under its own timeout. Hits are mapped back to governed
objects through `external_refs`; anything unknown, no longer effective, outside its validity window
or outside the caller's scope is dropped. That is the safety net: even while sync lags behind,
deprecated knowledge is never served. The request's filters (memory types, tags, metadata) apply
after it. Results are fused by reciprocal rank, weighted by trust, importance and time decay
(each item's weight is computed where it is resolved), thinned by MMR, put after the caller's
core memories (pinned, profile values) and cut to the token budget (design §6, `ranking`).
Defaults live in code; the namespace config (`retrieval.*`, `memory.types[].half_life_days`)
overrides them.
"""

import asyncio
import dataclasses
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from jinja2 import Environment, PackageLoader
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from llm_steward.errors import ProviderUnavailable, Unprocessable
from llm_steward.ids import uuid7
from llm_steward.models import (
    ExternalRef,
    KbCollection,
    KbDocument,
    KbEntry,
    Memory,
    RetrievalLog,
)
from llm_steward.models.base import utcnow
from llm_steward.models.enums import (
    EFFECTIVE_KB_STATUSES,
    DocStatus,
    KbStatus,
    MemoryStatus,
    ObjectType,
    SyncStatus,
)
from llm_steward.providers.base import ProviderHit, Providers, Scope
from llm_steward.ranking import (
    IMPORTANCE_BOOST,
    estimate_tokens,
    fit_budget,
    mmr,
    rrf_scores,
    similarity_terms,
    weigh,
)
from llm_steward.services.governance_service import namespace_config
from llm_steward.services.memory_service import core_memories
from llm_steward.services.scoping import visible

log = logging.getLogger(__name__)

# Ask each provider for this many candidates per requested result: the safety net may drop some.
CANDIDATES_PER_RESULT = 3
MIN_CANDIDATES = 10
Source = Literal["memory", "kb"]
ItemKind = Literal["memory", "entry", "document"]
_MEMORY_CURRENT = (MemoryStatus.ACTIVE,)
_MEMORY_HISTORY = (MemoryStatus.ACTIVE, MemoryStatus.SUPERSEDED)  # deleted stays out
_KB_HISTORY = (*EFFECTIVE_KB_STATUSES, KbStatus.DEPRECATED)

# Scoring defaults (design §6); the namespace config overrides them (`Scoring.from_config`).
TOP_K = 8
TOKEN_BUDGET = 2000
FILTER_WIDENING = 3  # filters run on what the providers return: ask them for more
MMR_POOL = 3  # MMR picks from the best top_k * MMR_POOL fused items
TRUST = {"verified": 1.0, "active": 0.9}
LOW_CONFIDENCE, LOW_CONFIDENCE_FACTOR = 0.5, 0.8  # an unsure memory counts less
KB_IMPORTANCE = 0.5  # KB items have no importance of their own: neutral
HALF_LIVES = {"episodic": 30.0}  # days; other memory types and KB never decay


@dataclass(frozen=True)
class Scoring:
    top_k: int = TOP_K
    token_budget: int = TOKEN_BUDGET
    trust: dict[str, float] = field(default_factory=lambda: dict(TRUST))
    importance_boost: float = IMPORTANCE_BOOST
    half_lives: dict[str, float] = field(default_factory=lambda: dict(HALF_LIVES))

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "Scoring":
        retrieval = config.get("retrieval") or {}
        types = (config.get("memory") or {}).get("types") or []
        half_lives = dict(HALF_LIVES) | {
            t["name"]: float(t["half_life_days"])
            for t in types
            if isinstance(t, dict) and t.get("name") and t.get("half_life_days")
        }
        return cls(
            top_k=int(retrieval.get("top_k", TOP_K)),
            token_budget=int(retrieval.get("token_budget", TOKEN_BUDGET)),
            trust=dict(TRUST) | dict(retrieval.get("trust_weights") or {}),
            importance_boost=float(retrieval.get("importance_boost", IMPORTANCE_BOOST)),
            half_lives=half_lives,
        )

    def memory_weight(self, m: Memory, now: datetime) -> float:
        """trust (an unsure memory counts less) x importance x decay by its type's half-life."""
        unsure = LOW_CONFIDENCE_FACTOR if m.confidence < LOW_CONFIDENCE else 1.0
        return weigh(
            1.0,
            trust=self.trust["active"] * unsure,
            importance=m.importance,
            age_days=(now - m.valid_from).total_seconds() / 86400,
            half_life_days=self.half_lives.get(m.memory_type),
            boost=self.importance_boost,
        )

    def kb_weight(self, verified: bool) -> float:
        """KB items: trust only, neutral importance, and knowledge does not decay with age."""
        trust = self.trust["verified" if verified else "active"]
        return trust * (1 + self.importance_boost * KB_IMPORTANCE)


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
    top_k: int | None = None  # None: the namespace's (default 8)
    with_context: bool = False
    # Retrieve what was valid at that moment (superseded memories included); no pinned prepend.
    as_of: datetime | None = None
    memory_types: tuple[str, ...] | None = None  # None: every type
    tags: tuple[str, ...] = ()  # every one of them (AND); KB documents have none
    metadata: dict[str, Any] = field(default_factory=dict)  # exact values; documents have none
    include_pinned: bool = True
    rerank: bool = False
    token_budget: int | None = None  # None: the namespace's (default 2000)

    @property
    def filtered(self) -> bool:
        return self.memory_types is not None or bool(self.tags) or bool(self.metadata)

    def matches(self, tags: list[str], meta: dict[str, Any]) -> bool:
        return set(self.tags) <= set(tags) and all(
            k in meta and meta[k] == v for k, v in self.metadata.items()
        )


@dataclass(frozen=True)
class RetrievedItem:
    source: Source
    id: uuid.UUID
    content: str
    title: str | None
    status: str
    verified: bool
    score: float = 0.0
    pinned: bool = False  # always-present core memory (design §6 step 8), not a search hit
    kind: ItemKind = field(kw_only=True)  # a KB hit is an entry or a chunk of a document
    weight: float = field(default=1.0, kw_only=True)  # trust x importance x decay (step 6)


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
        if req.rerank:
            raise Unprocessable("rerank is not available: no reranker is configured")
        scope = Scope(namespace_id, req.user_id, req.agent_id, req.session_id)
        async with self._sessions() as s:  # one session for both reads before the search
            scoring = Scoring.from_config(await namespace_config(s, namespace_id))
            collection_ext_ids = (
                await self._collection_ext_ids(s, namespace_id, req.collections)
                if "kb" in req.sources
                else []
            )
        top_k = req.top_k or scoring.top_k
        # Filters run after the safety net, on what the providers returned: ask for more.
        widen = FILTER_WIDENING if req.filtered else 1
        candidates = max(top_k * CANDIDATES_PER_RESULT * widen, MIN_CANDIDATES)
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

        now = req.as_of or utcnow()  # one clock for validity and age
        async with self._sessions() as s:
            rankings = await self._resolve(s, namespace_id, scope, hits, req, scoring, now)
            core = req.include_pinned and req.as_of is None
            pinned = _core_items(await core_memories(s, scope)) if core else []
        # Ranking is CPU work: no connection is held while it runs.
        seen = {p.id for p in pinned}
        ranked = [i for i in _fuse(rankings) if i.id not in seen]
        cost = _line_cost if req.with_context else _text_cost
        items = _select(pinned, ranked, top_k, req.token_budget or scoring.token_budget, cost)
        async with self._sessions() as s, s.begin():
            retrieval_id = self._log(s, namespace_id, req, items, latency_ms)
        context = _render(items) if req.with_context else None
        return RetrieveResult(retrieval_id, items, degraded, context)

    async def _timed(self, search: Any) -> tuple[Any, int]:
        started = time.perf_counter()
        try:
            async with asyncio.timeout(self._timeout_s):
                result = await search
        except Exception as exc:
            result = exc
        return result, int((time.perf_counter() - started) * 1000)

    async def _collection_ext_ids(
        self, s: AsyncSession, namespace_id: uuid.UUID, names: tuple[str, ...] | None
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
        return [eid for eid in await s.scalars(query) if eid]

    async def _resolve(
        self,
        s: AsyncSession,
        namespace_id: uuid.UUID,
        scope: Scope,
        hits: dict[Source, list[ProviderHit]],
        req: RetrieveRequest,
        scoring: Scoring,
        now: datetime,
    ) -> list[list[RetrievedItem]]:
        """Map provider hits to governed objects, keeping each provider's order; drop whatever
        governance would not serve (at `as_of`, what was valid then)."""
        rankings: list[list[RetrievedItem]] = []
        if hits.get("memory"):
            known = await self._memories(s, namespace_id, scope, hits["memory"], req, scoring, now)
            rankings.append(
                [known[h.external_id] for h in hits["memory"] if h.external_id in known]
            )
        if hits.get("kb"):
            known = await self._entries(s, namespace_id, hits["kb"], req, scoring, now)
            rest = [h for h in hits["kb"] if h.external_id not in known]
            if rest:  # not an entry: maybe a document's chunk
                known |= await self._documents(s, namespace_id, rest, req, scoring, now)
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
        req: RetrieveRequest,
        scoring: Scoring,
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
                *visible(Memory, scope, session=True),
                Memory.status.in_(_MEMORY_HISTORY if req.as_of else _MEMORY_CURRENT),
                Memory.valid_from <= now,
                or_(Memory.valid_to.is_(None), Memory.valid_to > now),
                or_(Memory.expires_at.is_(None), Memory.expires_at > now),
            )
        )
        if req.memory_types is not None:
            query = query.where(Memory.memory_type.in_(req.memory_types))
        rows = await s.execute(query)
        return {
            eid: RetrievedItem(
                "memory",
                m.id,
                m.content,
                None,
                m.status.value,
                False,
                kind="memory",
                weight=scoring.memory_weight(m, now),
            )
            for eid, m in rows
            if eid and req.matches(m.tags, m.meta)
        }

    async def _entries(
        self,
        s: AsyncSession,
        namespace_id: uuid.UUID,
        hits: list[ProviderHit],
        req: RetrieveRequest,
        scoring: Scoring,
        now: datetime,
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
                KbEntry.status.in_(_KB_HISTORY if req.as_of else EFFECTIVE_KB_STATUSES),
                KbEntry.valid_from <= now,
                or_(KbEntry.valid_to.is_(None), KbEntry.valid_to > now),
            )
        )
        rows = await s.execute(query)
        return {
            eid: RetrievedItem(
                "kb",
                e.id,
                e.content,
                e.title,
                e.status.value,
                e.status == KbStatus.VERIFIED,
                kind="entry",
                weight=scoring.kb_weight(e.status == KbStatus.VERIFIED),
            )
            for eid, e in rows
            if eid and req.matches(e.tags, e.meta)
        }

    async def _documents(
        self,
        s: AsyncSession,
        namespace_id: uuid.UUID,
        hits: list[ProviderHit],
        req: RetrieveRequest,
        scoring: Scoring,
        now: datetime,
    ) -> dict[str, RetrievedItem]:
        """Hits on document chunks: served only for a live (or, at `as_of`, then live) document
        the engine has finished parsing. The content is the matching chunk, not the file. A
        document has no tags or metadata: a request that filters on them gets none."""
        if req.tags or req.metadata:
            return {}
        query = (
            select(ExternalRef.external_id, KbDocument)
            .join(KbDocument, KbDocument.id == ExternalRef.object_id)
            .where(
                ExternalRef.object_type == ObjectType.KB_DOCUMENT,
                ExternalRef.provider == self._providers.kb.name,
                ExternalRef.namespace_id == namespace_id,
                ExternalRef.external_id.in_([h.external_id for h in hits]),
                KbDocument.namespace_id == namespace_id,
                KbDocument.state.in_(_KB_HISTORY if req.as_of else EFFECTIVE_KB_STATUSES),
                KbDocument.status == DocStatus.READY,
                KbDocument.created_at <= now,
                or_(KbDocument.valid_to.is_(None), KbDocument.valid_to > now),
            )
        )
        documents = {eid: d for eid, d in await s.execute(query) if eid}
        return {
            h.external_id: RetrievedItem(
                "kb",
                d.id,
                h.text,
                d.title,
                d.state.value,
                d.state == KbStatus.VERIFIED,
                kind="document",
                weight=scoring.kb_weight(d.state == KbStatus.VERIFIED),
            )
            for h in hits
            if (d := documents.get(h.external_id)) is not None
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
                "memory_types": list(req.memory_types) if req.memory_types else None,
                "tags": list(req.tags),
                "metadata": req.metadata,
                "token_budget": req.token_budget,
            },
            results=[{"source": i.source, "id": str(i.id), "score": i.score} for i in items],
            latency_ms=latency_ms,
        )
        s.add(entry)
        return entry.id


def _fuse(rankings: list[list[RetrievedItem]]) -> list[RetrievedItem]:
    """Reciprocal rank fusion (provider scores are not comparable across engines, ranks are),
    times each item's weight; best first, ties by id."""
    scores = rrf_scores([[item.id for item in ranking] for ranking in rankings])
    items = {item.id: item for ranking in rankings for item in ranking}
    weighted = [
        dataclasses.replace(items[i], score=rel * items[i].weight) for i, rel in scores.items()
    ]
    return sorted(weighted, key=lambda item: (-item.score, item.id))


def _select(
    pinned: list[RetrievedItem],
    ranked: list[RetrievedItem],
    top_k: int,
    budget: int,
    cost: Callable[[RetrievedItem], int],
) -> list[RetrievedItem]:
    """Pinned first (they count against the budget, not against `top_k`), then up to `top_k`
    of the best fused items in MMR order. An item too long for what is left of the budget is
    passed over (and does not count for diversity): the next one that fits takes its place."""
    kept = fit_budget(pinned, cost, budget)
    left = budget - sum(cost(i) for i in kept)
    pool = {i.id: i for i in ranked[: top_k * MMR_POOL]}

    def fits(item_id: uuid.UUID) -> bool:
        nonlocal left
        c = cost(pool[item_id])
        if c > left:
            return False
        left -= c
        return True

    terms = [(i.id, i.score, similarity_terms(_text(i))) for i in pool.values()]
    return kept + [pool[i] for i in mmr(terms, top_k, accept=fits)]


def _text(item: RetrievedItem) -> str:
    return f"{item.title or ''} {item.content}"


def _text_cost(item: RetrievedItem) -> int:
    return estimate_tokens(_text(item))


def _line_cost(item: RetrievedItem) -> int:
    """For format=context: the item as rendered (its heading included, so a context can never
    exceed the budget: the full render shares headings)."""
    return estimate_tokens(_render([item]))


def _render(items: list[RetrievedItem]) -> str:
    return (
        _templates.get_template("context_render.j2")
        .render(
            memories=[i for i in items if i.source == "memory"],
            kb=[i for i in items if i.source == "kb"],
        )
        .strip()
    )


def _core_items(memories: list[Memory]) -> list[RetrievedItem]:
    return [
        RetrievedItem(
            "memory", m.id, m.content, m.key, m.status.value, False, 1.0, True, kind="memory"
        )
        for m in memories
    ]
