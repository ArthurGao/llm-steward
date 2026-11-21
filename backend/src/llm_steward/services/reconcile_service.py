"""Drift check between a memory provider and the governance mirror (design §4 consistency rules).

- A synced active memory the provider lost → its ref is reset and an upsert re-queued.
- A provider record no mirror row points at → reported (and logged), never deleted automatically:
  it may be the leftover of an extraction that died before mirroring, and a person should look.
"""

import logging
import uuid
from dataclasses import dataclass, field

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from llm_steward.models import ExternalRef, Job, Memory, Namespace, ReconcileFinding
from llm_steward.models.enums import (
    ActorType,
    JobStatus,
    JobType,
    MemoryOrigin,
    MemoryStatus,
    ObjectType,
    SyncStatus,
)
from llm_steward.providers.base import MemoryProvider, Scope
from llm_steward.services.governance_service import Actor, GovernanceService, RecordProviderMemory
from llm_steward.services.job_queue import ClaimedJob, enqueue
from llm_steward.storage.db import run_unscoped

log = logging.getLogger(__name__)


@dataclass
class ReconcileReport:
    checked: int = 0
    requeued: list[uuid.UUID] = field(default_factory=list)
    orphans: list[str] = field(default_factory=list)


class ReconcileService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        provider: MemoryProvider,
        *,
        triage: bool = False,
        governance: GovernanceService | None = None,
    ) -> None:
        """`triage`: unmirrored records are handed to the triage assistant (P11). `governance`:
        the single writer, for a remirror a person decided on (P13)."""
        self._sessions = sessions
        self._provider = provider
        self._triage = triage
        self._governance = governance

    async def remirror(self, job: ClaimedJob) -> uuid.UUID | None:
        """A decided finding's record brought under governance (P13): read from the engine (it
        must still be this namespace's), then mirrored as an already-synced memory through the
        governance service. A record that is gone, or mirrored meanwhile, is left alone."""
        assert self._governance is not None
        finding_id = uuid.UUID(job.payload["finding_id"])
        async with self._sessions() as s:
            finding = await s.get(ReconcileFinding, finding_id)
            if finding is None or finding.namespace_id != job.namespace_id:
                return None
            mirrored = await s.scalar(
                select(ExternalRef.id).where(
                    ExternalRef.namespace_id == job.namespace_id,
                    ExternalRef.provider == finding.provider,
                    ExternalRef.external_id == finding.external_id,
                )
            )
        record = await self._provider.get(finding.external_id)
        if mirrored is not None or record is None or record.scope.namespace_id != job.namespace_id:
            log.info("remirror %s: nothing to do (mirrored or gone)", finding.external_id)
            return None
        op = RecordProviderMemory(
            job.namespace_id,
            record.external_id,
            record.content,
            user_id=record.scope.user_id,
            agent_id=record.scope.agent_id,
            session_id=record.scope.session_id,
            origin=MemoryOrigin.IMPORT,
        )
        actor = Actor(ActorType.ADMIN, finding.decided_by)  # set when a person decided
        memory: Memory = await self._governance.apply(op, actor)
        return memory.id

    async def handle(self, job: ClaimedJob) -> ReconcileReport:
        return await self.run(job.namespace_id)

    async def run(self, namespace_id: uuid.UUID) -> ReconcileReport:
        report = ReconcileReport()
        async with self._sessions() as s:
            users = set(
                await s.scalars(
                    select(Memory.user_id).where(Memory.namespace_id == namespace_id).distinct()
                )
            )
            refs = (
                await s.execute(
                    select(ExternalRef.external_id, Memory.id, Memory.status)
                    .join(Memory, Memory.id == ExternalRef.object_id)
                    .where(
                        ExternalRef.object_type == ObjectType.MEMORY,
                        ExternalRef.provider == self._provider.name,
                        ExternalRef.namespace_id == namespace_id,
                    )
                )
            ).all()
        in_provider: set[str] = set()
        for user_id in users | {None}:  # engines list per user; None = the shared memories
            records = await self._provider.list(Scope(namespace_id, user_id=user_id))
            in_provider |= {r.external_id for r in records}

        mirrored = {eid for eid, _, _ in refs if eid}
        report.checked = len(mirrored)
        lost = [
            mid
            for eid, mid, status in refs
            if eid and eid not in in_provider and status == MemoryStatus.ACTIVE
        ]
        report.orphans = sorted(in_provider - mirrored)
        triage = bool(report.orphans) and self._triage
        if lost or triage:
            async with self._sessions() as s, s.begin():
                if lost:
                    await s.execute(
                        update(ExternalRef)
                        .where(
                            ExternalRef.object_type == ObjectType.MEMORY,
                            ExternalRef.provider == self._provider.name,
                            ExternalRef.object_id.in_(lost),
                        )
                        .values(external_id=None, sync_status=SyncStatus.PENDING)
                    )
                    for memory_id in lost:
                        enqueue(
                            s, namespace_id, JobType.MEMORY_UPSERT, {"memory_id": str(memory_id)}
                        )
                if triage:
                    enqueue(s, namespace_id, JobType.RECONCILE_TRIAGE, {"orphans": report.orphans})
            report.requeued = lost
        if lost or report.orphans:
            log.warning(
                "reconcile %s: %d lost by the provider (re-queued), %d provider records unmirrored",
                namespace_id,
                len(lost),
                len(report.orphans),
            )
        return report


@run_unscoped  # every namespace's jobs (P12)
async def schedule_reconcile(sessions: async_sessionmaker[AsyncSession]) -> int:
    """Queue one reconcile job per namespace unless one is already waiting or running."""
    queued = 0
    async with sessions() as s, s.begin():
        busy = set(
            await s.scalars(
                select(Job.namespace_id).where(
                    Job.type == JobType.MEMORY_RECONCILE,
                    Job.status.in_([JobStatus.PENDING, JobStatus.RUNNING]),
                )
            )
        )
        for namespace_id in await s.scalars(select(Namespace.id)):
            if namespace_id not in busy:
                enqueue(s, namespace_id, JobType.MEMORY_RECONCILE, {})
                queued += 1
    return queued
