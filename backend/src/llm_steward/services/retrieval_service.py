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
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from jinja2 import Environment, PackageLoader
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from llm_steward.errors import ProviderUnavailable, Unprocessable
from llm_steward.ids import uuid7
from llm_steward.models import (
    ExternalRef,
    KbCollection,
    KbDocument,
    KbEntry,
    Memory,
    RetrievalFeedback,
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
from llm_steward.pipeline.risk import redact_pii
from llm_steward.providers.base import ProviderHit, Providers, Reranker, Scope
from llm_steward.ranking import (
    IMPORTANCE_BOOST,
    estimate_tokens,
    fit_budget,
    mmr,
    rrf_scores,
    similarity_terms,
    weigh,
)
from llm_steward.services.governance_service import load_owned, namespace_config
from llm_steward.services.memory_service import core_memories
from llm_steward.services.scoping import visible

log = logging.getLogger(__name__)

# Ask each provider for this many candidates per requested result: the safety net may drop some.
CANDIDATES_PER_RESULT = 3
MIN_CANDIDATES = 10
Source = Literal["memory", "kb"]
Degraded = Literal["memory", "kb", "rerank"]  # what failed or ran out of time
ItemKind = Literal["memory", "entry", "document"]
_MEMORY_CURRENT = (MemoryStatus.ACTIVE,)
_MEMORY_HISTORY = (MemoryStatus.ACTIVE, MemoryStatus.SUPERSEDED)  # deleted stays out
_KB_HISTORY = (*EFFECTIVE_KB_STATUSES, KbStatus.DEPRECATED)

# Scoring defaults (design §6); the namespace config overrides them (`Scoring.from_config`).
TOP_K = 8
TOKEN_BUDGET = 2000
FILTER_WIDENING = 3  # filters run on what the providers return: ask them for more
MMR_POOL = 3  # MMR picks from the best top_k * MMR_POOL fused items
CHUNK_SEPARATOR = "\n…\n"  # between a document's chunks in one item
RERANK_TOP_N = 30  # design §6 step 5
HIT_BATCH = 500  # retrievals counted per pass of the hit-count tick
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
    rel: float = field(default=0.0, kw_only=True)  # fused (RRF) relevance before the weight
    # a document's matching chunks, best first; `content` is them joined (CHUNK_SEPARATOR)
    passages: tuple[str, ...] = field(default=(), kw_only=True)


@dataclass(frozen=True)
class RetrieveResult:
    retrieval_id: uuid.UUID
    items: list[RetrievedItem]
    degraded: list[Degraded] = field(default_factory=list)
    context: str | None = None


class RetrievalService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        providers: Providers,
        *,
        timeout_s: float,
        reranker: Reranker | None,
        rerank_timeout_s: float,
    ) -> None:
        self._sessions = sessions
        self._providers = providers
        self._timeout_s = timeout_s
        self._reranker = reranker
        self._rerank_timeout_s = rerank_timeout_s

    async def retrieve(self, namespace_id: uuid.UUID, req: RetrieveRequest) -> RetrieveResult:
        if req.rerank and self._reranker is None:
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
        degraded: list[Degraded] = []
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
        reranked_by = None
        if req.rerank and self._reranker is not None and ranked:
            ranked, reranked_by = await self._rerank(req.query, ranked, degraded, latency_ms)
        cost = _line_cost if req.with_context else _text_cost
        budget = req.token_budget or scoring.token_budget
        items, left_out = _select(pinned, ranked, top_k, budget, cost)
        ranks = {i.id: rank for r in rankings for rank, i in enumerate(r, start=1)}
        async with self._sessions() as s, s.begin():
            retrieval_id = self._log(
                s, namespace_id, req, items, left_out, ranks, latency_ms, reranked_by
            )
        context = _render(items) if req.with_context else None
        return RetrieveResult(retrieval_id, items, degraded, context)

    async def feedback(
        self,
        namespace_id: uuid.UUID,
        retrieval_id: uuid.UUID,
        item_id: uuid.UUID,
        *,
        helpful: bool,
        note: str | None,
        by: str | None,
    ) -> None:
        """Feedback on one returned item (design §7): a row of its own (the note redacted) and,
        for a KB entry, its helpful / unhelpful count, at once (feedback is rare: no queue).
        404: no such retrieval in this namespace; 422: the item was not one it returned."""
        async with self._sessions() as s, s.begin():
            log_row = await load_owned(
                s, RetrievalLog, namespace_id, retrieval_id, "retrieval", lock=False
            )
            returned = {r["id"]: r for r in log_row.results}
            row = returned.get(str(item_id))
            if row is None:
                raise Unprocessable(f"{item_id} was not returned by retrieval {retrieval_id}")
            s.add(
                RetrievalFeedback(
                    namespace_id=namespace_id,
                    retrieval_id=retrieval_id,
                    item_id=item_id,
                    kind=row["kind"],
                    helpful=helpful,
                    note=redact_pii(note) if note else None,
                    given_by=by,
                )
            )
            if row["kind"] == "entry":
                counter = KbEntry.helpful_count if helpful else KbEntry.unhelpful_count
                await s.execute(
                    update(KbEntry)
                    .where(KbEntry.namespace_id == namespace_id, KbEntry.id == item_id)
                    .values({counter: counter + 1, KbEntry.updated_at: KbEntry.updated_at})
                )

    async def count_hits(self, batch: int = HIT_BATCH) -> int:
        """Design §6 step 9, off the request path (a worker tick): the hits of up to `batch`
        uncounted retrievals, added to the counters in one update per object (sorted, so two
        workers never wait on each other in opposite orders), and the logs marked counted, in one
        transaction. Returns how many retrievals were counted.

        The counters are statistics, not governed state: written here directly, with no
        change_log and without touching `updated_at` (being read is not being edited)."""
        async with self._sessions() as s, s.begin():
            logs = list(
                await s.scalars(
                    select(RetrievalLog)
                    .where(RetrievalLog.counted.is_(False))
                    .order_by(RetrievalLog.id)
                    .limit(batch)
                    .with_for_update(skip_locked=True)
                )
            )
            hits: dict[str, Counter[tuple[uuid.UUID, uuid.UUID]]] = {
                "memory": Counter(),
                "entry": Counter(),
            }
            for log_row in logs:
                for r in log_row.results:
                    if r["kind"] in hits:
                        hits[r["kind"]][(log_row.namespace_id, uuid.UUID(r["id"]))] += 1
                log_row.counted = True
            now = utcnow()
            for (ns, memory_id), n in sorted(hits["memory"].items()):
                await s.execute(
                    update(Memory)
                    .where(Memory.namespace_id == ns, Memory.id == memory_id)
                    .values(
                        access_count=Memory.access_count + n,
                        last_accessed_at=now,
                        updated_at=Memory.updated_at,
                    )
                )
            for (ns, entry_id), n in sorted(hits["entry"].items()):
                await s.execute(
                    update(KbEntry)
                    .where(KbEntry.namespace_id == ns, KbEntry.id == entry_id)
                    .values(hit_count=KbEntry.hit_count + n, updated_at=KbEntry.updated_at)
                )
        return len(logs)

    async def _rerank(
        self,
        query: str,
        ranked: list[RetrievedItem],
        degraded: list[Degraded],
        latency_ms: dict[str, int],
    ) -> tuple[list[RetrievedItem], str | None]:
        """Design §6 step 5: the cross-encoder re-scores the best RERANK_TOP_N fused items; its
        relevance (in [0, 1]) replaces their fused one, still times their weight; the rest
        follow in fused order. Failing or out of time: the fused order stands, `degraded` says
        so. Returns the order and the model that set it (None: it did not)."""
        reranker = self._reranker
        assert reranker is not None  # checked by the caller
        head, tail = ranked[:RERANK_TOP_N], ranked[RERANK_TOP_N:]
        started = time.perf_counter()
        try:
            async with asyncio.timeout(self._rerank_timeout_s):
                relevance = await reranker.score(query, [_text(i) for i in head])
        except Exception as exc:
            log.warning("retrieve: rerank failed: %s", type(exc).__name__)
            degraded.append("rerank")
            return ranked, None
        finally:
            latency_ms["rerank"] = int((time.perf_counter() - started) * 1000)
        rescored = [
            dataclasses.replace(item, score=r * item.weight)
            for item, r in zip(head, relevance, strict=True)
        ]
        return sorted(rescored, key=lambda i: (-i.score, i.id)) + tail, reranker.model

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
        the engine has finished parsing. Its `passages` are its matching chunks (best first) and
        its content is them joined, never the file. A document has no tags or metadata: a
        request that filters on them gets none."""
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
            h.external_id: _with_passages(
                RetrievedItem(
                    "kb",
                    d.id,
                    h.text,
                    d.title,
                    d.state.value,
                    d.state == KbStatus.VERIFIED,
                    kind="document",
                    weight=scoring.kb_weight(d.state == KbStatus.VERIFIED),
                ),
                h.passages or (h.text,),
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
        left_out: list[tuple[RetrievedItem, str]],
        ranks: dict[uuid.UUID, int],
        latency_ms: dict[str, int],
        reranked_by: str | None,
    ) -> uuid.UUID:
        """What was asked and how each candidate fared (design §6 step 9): the returned items in
        `results`, the candidates left out (and why) in `left_out`, each with its rank in its
        source, its fused relevance and its final score, so a ranking can be explained later."""

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
                "rerank": req.rerank,
                "reranked_by": reranked_by,  # None when asked but it failed or timed out
            },
            results=[_log_row(i, ranks) for i in items],
            left_out=[{**_log_row(i, ranks), "why": why} for i, why in left_out],
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
        dataclasses.replace(items[i], score=rel * items[i].weight, rel=rel)
        for i, rel in scores.items()
    ]
    return sorted(weighted, key=lambda item: (-item.score, item.id))


def _select(
    pinned: list[RetrievedItem],
    ranked: list[RetrievedItem],
    top_k: int,
    budget: int,
    cost: Callable[[RetrievedItem], int],
) -> tuple[list[RetrievedItem], list[tuple[RetrievedItem, str]]]:
    """Pinned first (they count against the budget, not against `top_k`), then up to `top_k`
    of the best fused items in MMR order. An item too long for what is left of the budget is
    cut to its best passages if that fits, else passed over (not counting for diversity): the
    next one that fits takes its place. Also returns what was left out of the candidates
    considered, and why: "budget" (did not fit) or "rank" (others came first)."""
    kept = fit_budget(pinned, cost, budget)
    refused: set[uuid.UUID] = {p.id for p in pinned} - {p.id for p in kept}
    left = budget - sum(cost(i) for i in kept)
    pool = {i.id: i for i in ranked[: top_k * MMR_POOL]}
    fitted: dict[uuid.UUID, RetrievedItem] = {}

    def fits(item_id: uuid.UUID) -> bool:
        nonlocal left
        item = pool[item_id]
        for n in range(len(item.passages), 0, -1) if item.passages else (None,):
            variant = item if n is None else _with_passages(item, item.passages[:n])
            if (c := cost(variant)) <= left:
                fitted[item_id], left = variant, left - c
                return True
        refused.add(item_id)
        return False

    terms = [(i.id, i.score, similarity_terms(_text(i))) for i in pool.values()]
    picked = [fitted[i] for i in mmr(terms, top_k, accept=fits)]
    left_out = [
        (i, "budget" if i.id in refused else "rank")
        for i in [*pinned, *pool.values()]
        if i.id in refused or (i.id in pool and i.id not in fitted)
    ]
    return kept + picked, left_out


def _log_row(item: RetrievedItem, ranks: dict[uuid.UUID, int]) -> dict[str, Any]:
    return {
        "source": item.source,
        "kind": item.kind,
        "id": str(item.id),
        "score": item.score,
        "rel": item.rel,
        "rank": ranks.get(item.id),  # None for a pinned memory: not a search hit
        "pinned": item.pinned,
    }


def _with_passages(item: RetrievedItem, passages: tuple[str, ...]) -> RetrievedItem:
    return dataclasses.replace(item, passages=passages, content=CHUNK_SEPARATOR.join(passages))


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
