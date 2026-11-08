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

from llm_steward.models import ExternalRef, Job, Memory, Namespace
from llm_steward.models.enums import JobStatus, JobType, MemoryStatus, ObjectType, SyncStatus
from llm_steward.providers.base import MemoryProvider, Scope
from llm_steward.services.job_queue import ClaimedJob, enqueue

log = logging.getLogger(__name__)


@dataclass
class ReconcileReport:
    checked: int = 0
    requeued: list[uuid.UUID] = field(default_factory=list)
    orphans: list[str] = field(default_factory=list)


class ReconcileService:
    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], provider: MemoryProvider
    ) -> None:
        self._sessions = sessions
        self._provider = provider

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
        if lost:
            async with self._sessions() as s, s.begin():
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
                    enqueue(s, namespace_id, JobType.MEMORY_UPSERT, {"memory_id": str(memory_id)})
            report.requeued = lost
        if lost or report.orphans:
            log.warning(
                "reconcile %s: %d lost by the provider (re-queued), %d provider records unmirrored",
                namespace_id,
                len(lost),
                len(report.orphans),
            )
        return report


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
