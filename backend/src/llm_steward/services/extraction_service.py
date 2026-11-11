"""Episode -> memories (design §5 steps 2-4): the provider extracts, governance mirrors, conflict
compensation reconciles the new facts with what the scope already holds.

The provider's events are written to the mirror, in one transaction, right after `add` returns. A
retry (say the resolve call failed) resumes from that mirror instead of calling `add` again, so the
engine never receives a batch twice. If the process dies between `add` and the mirror commit, the
provider holds facts without a mirror row and a retry extracts again (the engine has no idempotency
key); the daily reconcile reports those orphans, and the retrieval safety net never serves them.
"""

import logging
import uuid
from collections.abc import Awaitable, Callable, Sequence
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from llm_steward.errors import Unprocessable
from llm_steward.ids import uuid7
from llm_steward.llm.client import LLM
from llm_steward.models import Episode, ExternalRef, Memory
from llm_steward.models.enums import ActorType, MemoryOrigin, MemoryStatus, ObjectType
from llm_steward.pipeline.conflict import Fact, resolve_conflict
from llm_steward.pipeline.extract import extract_facts
from llm_steward.providers.base import (
    EXTRACT,
    RESOLVES_CONFLICTS,
    MemoryEvent,
    MemoryProvider,
    Message,
    Scope,
)
from llm_steward.services.governance_service import (
    Actor,
    CreateMemory,
    DeleteMemory,
    GovernanceService,
    Operation,
    RecordProviderMemory,
    SupersedeMemory,
)
from llm_steward.services.job_queue import ClaimedJob
from llm_steward.services.scoping import exact

log = logging.getLogger(__name__)

CANDIDATES = 5


# Who turns conversations into memories: the engine on add, llm-steward for an engine that cannot
# (STEWARD_MEMORY_LLM), or nobody (direct writes only). Decided once, from capabilities, never by
# name (backend/AGENTS.md rule 4).
ExtractionMode = Literal["engine", "steward", "none"]


def extraction_mode(provider: MemoryProvider, memory_llm: bool) -> ExtractionMode:
    if EXTRACT in provider.capabilities:
        return "engine"
    return "steward" if memory_llm else "none"


def compensates(provider: MemoryProvider, mode: ExtractionMode) -> bool:
    """Conflict compensation runs whenever facts are extracted and the engine does not resolve
    conflicts on add itself."""
    return mode != "none" and RESOLVES_CONFLICTS not in provider.capabilities


class ExtractionService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        governance: GovernanceService,
        provider: MemoryProvider,
        llm: LLM | None,
        *,
        mode: ExtractionMode,
        on_extracted: Callable[[uuid.UUID, list[uuid.UUID]], Awaitable[None]] | None = None,
    ) -> None:
        """`mode`: see `extraction_mode`. `on_extracted(namespace, memory ids)`: what follows a
        batch (the KB suggest job)."""
        self._on_extracted = on_extracted
        self._sessions = sessions
        self._governance = governance
        self._provider = provider
        self.mode = mode
        self._compensates = compensates(provider, mode)
        if llm is None and mode == "steward":
            raise ValueError("llm-steward extracts memories (STEWARD_MEMORY_LLM): it needs an LLM")
        if llm is None and self._compensates:
            raise ValueError(f"the {provider.name} memory provider needs conflict compensation")
        self._llm = llm

    async def handle(self, job: ClaimedJob) -> list[uuid.UUID]:
        return await self.run(job.namespace_id, [uuid.UUID(i) for i in job.payload["episode_ids"]])

    async def run(
        self, namespace_id: uuid.UUID, episode_ids: Sequence[uuid.UUID]
    ) -> list[uuid.UUID]:
        """Extract from the episodes; returns the ids of the memories that stay active."""
        if self.mode == "none":
            raise Unprocessable(f"the {self._provider.name} memory provider cannot extract")
        episodes = await self._episodes(namespace_id, episode_ids)
        if not episodes:
            return []
        first, last = episodes[0], episodes[-1]
        scope = Scope(namespace_id, first.user_id, first.agent_id, first.session_id)
        mirrored = await self._mirrored(namespace_id, last.id)
        if mirrored is not None:  # a retry: this batch already went to the engine
            return await self._finish(scope, mirrored)
        said: list[dict[str, str]] = [m for e in episodes for m in e.content["messages"]]
        if self.mode == "steward":
            return await self._finish(scope, await self._extract_here(scope, said, last.id))
        messages = [Message(m["role"], m["content"]) for m in said]
        # The batch's last episode keys the engine's write: a retry after a timeout replays it
        # (an engine that honours keys) instead of extracting again.
        events = await self._provider.add(messages, scope, infer=True, idempotency_key=str(last.id))
        added = [e for e in events if e.op == "ADD" and e.content]
        retired = [e for e in events if e.op == "DELETE"]
        if not added and not retired:
            return []
        # Provenance of extraction (rule 10): the role and model that produced the facts.
        produced_by = {e.produced_by for e in added + retired} - {None}
        actor = (
            Actor(ActorType.AI, produced_by.pop())
            if len(produced_by) == 1
            else Actor(ActorType.PROVIDER, self._provider.name)
        )
        held = {e.replaces for e in added if e.replaces} | {e.external_id for e in retired}
        mirrors = await self._mirrors(namespace_id, held)
        ops = self._mirroring(scope, last.id, added, retired, mirrors)
        results = await self._governance.apply_all(ops, actor)
        created: list[Memory] = [
            r for r, op in zip(results, ops, strict=True) if isinstance(op, RecordProviderMemory)
        ]
        return await self._finish(scope, created)

    def _mirroring(
        self,
        scope: Scope,
        episode_id: uuid.UUID,
        added: list[MemoryEvent],
        retired: list[MemoryEvent],
        mirrors: dict[str, uuid.UUID],
    ) -> list[Operation]:
        """One batch of governance ops for an engine's events: each new fact is recorded; a fact
        that replaces one (an engine that resolves conflicts) supersedes its mirror with `by=` the
        new memory, and a retired one supersedes its mirror with no successor. An id with no
        active mirror is skipped: llm-steward never had it, or already invalidated it."""
        ns = scope.namespace_id
        ops: list[Operation] = []
        for e in added:
            new = uuid7()
            ops.append(
                RecordProviderMemory(
                    namespace_id=ns, external_id=e.external_id, content=e.content or "",
                    user_id=scope.user_id, agent_id=scope.agent_id, session_id=scope.session_id,
                    source_episode_id=episode_id, id=new,
                )
            )  # fmt: skip
            if e.replaces in mirrors:
                ops.append(SupersedeMemory(ns, mirrors[e.replaces], new, "UPDATE: by the engine"))
            elif e.replaces is not None:
                log.warning("engine replaced %s, which has no active mirror", e.replaces)
        for e in retired:
            if e.external_id in mirrors:
                ops.append(
                    SupersedeMemory(ns, mirrors[e.external_id], None, "DELETE: by the engine")
                )
            else:
                log.warning("engine retired %s, which has no active mirror", e.external_id)
        return ops

    async def _mirrors(
        self, namespace_id: uuid.UUID, external_ids: set[str]
    ) -> dict[str, uuid.UUID]:
        """The active memories that mirror these engine ids."""
        if not external_ids:
            return {}
        async with self._sessions() as s:
            rows = await s.execute(
                select(ExternalRef.external_id, Memory.id)
                .join(Memory, Memory.id == ExternalRef.object_id)
                .where(
                    ExternalRef.namespace_id == namespace_id,
                    ExternalRef.object_type == ObjectType.MEMORY,
                    ExternalRef.provider == self._provider.name,
                    ExternalRef.external_id.in_(external_ids),
                    Memory.namespace_id == namespace_id,
                    Memory.status == MemoryStatus.ACTIVE,
                )
            )
            return {str(external_id): memory_id for external_id, memory_id in rows.all()}

    async def _extract_here(
        self, scope: Scope, messages: list[dict[str, str]], last: uuid.UUID
    ) -> list[Memory]:
        """The facts the memory.extract role finds, written as memories in one governance
        transaction (the Syncer pushes them to the engine): a retry finds them in the mirror and
        never asks the model again."""
        assert self._llm is not None
        facts, provenance = await extract_facts(self._llm, messages)
        if not facts:
            return []
        ops: list[Operation] = [
            CreateMemory(
                scope.namespace_id,
                fact,
                user_id=scope.user_id,
                agent_id=scope.agent_id,
                session_id=scope.session_id,
                origin=MemoryOrigin.EXTRACTION,
                source_episode_id=last,
            )
            for fact in facts
        ]
        return await self._governance.apply_all(ops, Actor(ActorType.AI, provenance.tag))

    async def _finish(self, scope: Scope, created: list[Memory]) -> list[uuid.UUID]:
        kept = (
            await self._compensate(scope, created) if self._compensates else [m.id for m in created]
        )
        if self._on_extracted is not None:
            await self._on_extracted(scope.namespace_id, kept)
        return kept

    async def _mirrored(self, namespace_id: uuid.UUID, last: uuid.UUID) -> list[Memory] | None:
        """The still-active memories an earlier attempt mirrored for this batch (each batch has its
        own last episode), or None when there was no earlier attempt that got that far. A fact
        already resolved as UPDATE/DELETE stays active and is judged again: harmless, since what
        it replaced is no longer active; a NOOP duplicate is already gone."""
        async with self._sessions() as s:
            rows = list(
                await s.scalars(
                    select(Memory)
                    .where(
                        Memory.namespace_id == namespace_id,
                        Memory.source_episode_id == last,
                        Memory.origin == MemoryOrigin.EXTRACTION,
                    )
                    .order_by(Memory.id)
                )
            )
        if not rows:
            return None
        return [m for m in rows if m.status == MemoryStatus.ACTIVE]

    async def _compensate(self, scope: Scope, created: list[Memory]) -> list[uuid.UUID]:
        assert self._llm is not None
        batch = {m.id for m in created}
        kept: list[uuid.UUID] = []
        for memory in created:
            existing = await self._related(scope, memory, exclude=batch)
            resolution = await resolve_conflict(
                self._llm,
                Fact(str(memory.id), memory.content),
                [Fact(str(m.id), m.content) for m in existing],
            )
            actor = (
                Actor(ActorType.AI, resolution.provenance.tag)
                if resolution.provenance
                else Actor(ActorType.SYSTEM, "conflict-compensation")
            )
            reason = f"{resolution.action}: {resolution.reason}"
            ns = scope.namespace_id
            if resolution.action == "NOOP":
                await self._governance.apply(DeleteMemory(ns, memory.id, reason), actor)
                continue
            if resolution.target_id is not None:  # UPDATE (replaced by this one) or DELETE
                by = memory.id if resolution.action == "UPDATE" else None
                op = SupersedeMemory(ns, uuid.UUID(resolution.target_id), by, reason)
                await self._governance.apply(op, actor)
            kept.append(memory.id)
        return kept

    async def _related(self, scope: Scope, memory: Memory, exclude: set[uuid.UUID]) -> list[Memory]:
        """Active memories of exactly this scope that the provider finds close to the new one."""
        hits = await self._provider.search(memory.content, scope, top_k=CANDIDATES + len(exclude))
        if not hits:
            return []
        async with self._sessions() as s:
            rows = await s.scalars(
                select(Memory)
                .join(ExternalRef, ExternalRef.object_id == Memory.id)
                .where(
                    ExternalRef.object_type == ObjectType.MEMORY,
                    ExternalRef.provider == self._provider.name,
                    ExternalRef.external_id.in_([h.external_id for h in hits]),
                    *exact(Memory, scope, session=False),
                    Memory.status == MemoryStatus.ACTIVE,
                    Memory.id.not_in(exclude),
                )
            )
            return list(rows)[:CANDIDATES]

    async def _episodes(
        self, namespace_id: uuid.UUID, episode_ids: Sequence[uuid.UUID]
    ) -> list[Episode]:
        async with self._sessions() as s:
            rows = await s.scalars(
                select(Episode)
                .where(Episode.namespace_id == namespace_id, Episode.id.in_(episode_ids))
                .order_by(Episode.id)
            )
            return list(rows)
