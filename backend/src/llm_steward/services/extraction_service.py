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
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from llm_steward.errors import Unprocessable
from llm_steward.llm.client import LLM
from llm_steward.models import Episode, ExternalRef, Memory
from llm_steward.models.enums import ActorType, MemoryOrigin, MemoryStatus, ObjectType
from llm_steward.pipeline.conflict import Fact, resolve_conflict
from llm_steward.providers.base import (
    EXTRACT,
    RESOLVES_CONFLICTS,
    MemoryProvider,
    Message,
    Scope,
)
from llm_steward.services.governance_service import (
    Actor,
    DeleteMemory,
    GovernanceService,
    RecordProviderMemory,
    SupersedeMemory,
)
from llm_steward.services.job_queue import ClaimedJob
from llm_steward.services.scoping import exact

log = logging.getLogger(__name__)

CANDIDATES = 5


def compensates(provider: MemoryProvider) -> bool:
    """Conflict compensation runs for an engine that extracts but does not resolve conflicts on
    add (capability-driven, never by name; backend/AGENTS.md rule 4)."""
    caps = provider.capabilities
    return EXTRACT in caps and RESOLVES_CONFLICTS not in caps


class ExtractionService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        governance: GovernanceService,
        provider: MemoryProvider,
        llm: LLM | None,
    ) -> None:
        self._sessions = sessions
        self._governance = governance
        self._provider = provider
        if compensates(provider) and llm is None:
            raise ValueError(f"the {provider.name} memory provider needs conflict compensation")
        self._llm = llm

    async def handle(self, job: ClaimedJob) -> list[uuid.UUID]:
        return await self.run(job.namespace_id, [uuid.UUID(i) for i in job.payload["episode_ids"]])

    async def run(
        self, namespace_id: uuid.UUID, episode_ids: Sequence[uuid.UUID]
    ) -> list[uuid.UUID]:
        """Extract from the episodes; returns the ids of the memories that stay active."""
        if EXTRACT not in self._provider.capabilities:
            raise Unprocessable(f"the {self._provider.name} memory provider cannot extract")
        episodes = await self._episodes(namespace_id, episode_ids)
        if not episodes:
            return []
        first, last = episodes[0], episodes[-1]
        scope = Scope(namespace_id, first.user_id, first.agent_id, first.session_id)
        mirrored = await self._mirrored(namespace_id, last.id)
        if mirrored is not None:  # a retry: this batch already went to the engine
            return await self._finish(scope, mirrored)
        messages = [
            Message(m["role"], m["content"]) for e in episodes for m in e.content["messages"]
        ]
        added = [
            e
            for e in await self._provider.add(messages, scope, infer=True)
            if e.op == "ADD" and e.content
        ]
        if not added:
            return []
        # Provenance of extraction (rule 10): the role and model that produced the facts.
        produced_by = {e.produced_by for e in added} - {None}
        actor = (
            Actor(ActorType.AI, produced_by.pop())
            if len(produced_by) == 1
            else Actor(ActorType.PROVIDER, self._provider.name)
        )
        created: list[Memory] = await self._governance.apply_all(
            [
                RecordProviderMemory(
                    namespace_id=namespace_id,
                    external_id=e.external_id,
                    content=e.content or "",
                    user_id=scope.user_id,
                    agent_id=scope.agent_id,
                    session_id=scope.session_id,
                    source_episode_id=last.id,
                )
                for e in added
            ],
            actor,
        )
        return await self._finish(scope, created)

    async def _finish(self, scope: Scope, created: list[Memory]) -> list[uuid.UUID]:
        if not compensates(self._provider):
            return [m.id for m in created]
        return await self._compensate(scope, created)

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
                Actor(ActorType.AI, f"{resolution.provenance.role}:{resolution.provenance.model}")
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
