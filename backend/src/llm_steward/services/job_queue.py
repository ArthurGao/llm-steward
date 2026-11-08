"""Outbox on the `jobs` table: enqueue inside the writer's transaction, claim with a lease.

Claiming is a conditional UPDATE (`... WHERE id = :id AND <still claimable>`), so two workers can
never both win a job on either dialect; PostgreSQL additionally skips rows another claimer locked.
Every claim increments `attempts`, which doubles as the claim's fencing token: complete/fail only
apply while the job is still on the same claim, so a worker whose lease ran out (and whose job was
reclaimed) can no longer settle someone else's claim.
"""

import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from llm_steward.models import Job
from llm_steward.models.base import utcnow
from llm_steward.models.enums import JobStatus, JobType

ERROR_MAX_CHARS = 500


@dataclass(frozen=True)
class ClaimedJob:
    id: uuid.UUID
    namespace_id: uuid.UUID
    type: JobType
    payload: dict[str, Any]
    attempts: int


def enqueue(
    session: AsyncSession,
    namespace_id: uuid.UUID,
    job_type: JobType,
    payload: dict[str, Any],
    *,
    delay: timedelta | None = None,
) -> Job:
    """Add an outbox row to the caller's session; it commits (or not) with the caller's write.
    `delay`: not claimable before then (a poll)."""
    job = Job(
        namespace_id=namespace_id,
        type=job_type,
        payload=payload,
        status=JobStatus.PENDING,
        run_after=utcnow() + (delay or timedelta()),
    )
    session.add(job)
    return job


def describe_error(exc: BaseException) -> str:
    """Exception type plus a truncated message: enough to debug, too short to carry a payload."""
    return f"{type(exc).__name__}: {exc}"[:ERROR_MAX_CHARS]


class JobQueue:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        lease: timedelta = timedelta(seconds=60),
        backoff_base_s: float = 2.0,
        backoff_cap_s: float = 300.0,
    ) -> None:
        self._sessions = sessions
        self._lease = lease
        self._backoff_base_s = backoff_base_s
        self._backoff_cap_s = backoff_cap_s

    async def claim(self, job_id: uuid.UUID | None = None) -> ClaimedJob | None:
        """The next claimable job, or that specific one (a request processing its own job)."""
        now = utcnow()
        claimable = or_(
            and_(Job.status == JobStatus.PENDING, Job.run_after <= now),
            # A worker that died mid-job leaves an expired lease: the job is claimable again.
            and_(Job.status == JobStatus.RUNNING, Job.locked_until < now),
        )
        async with self._sessions() as session, session.begin():
            candidates = select(Job.id).where(claimable)
            if job_id is not None:
                candidates = candidates.where(Job.id == job_id)
            picked = await session.scalar(
                candidates.order_by(Job.run_after).limit(1).with_for_update(skip_locked=True)
            )
            if picked is None:
                return None
            row = (
                await session.execute(
                    update(Job)
                    .where(Job.id == picked, claimable)
                    .values(
                        status=JobStatus.RUNNING,
                        locked_until=now + self._lease,
                        attempts=Job.attempts + 1,
                    )
                    .returning(Job.id, Job.namespace_id, Job.type, Job.payload, Job.attempts)
                )
            ).first()
        if row is None:  # another worker won the race
            return None
        return ClaimedJob(row.id, row.namespace_id, JobType(row.type), row.payload, row.attempts)

    @property
    def lease(self) -> timedelta:
        return self._lease

    async def complete(self, job: ClaimedJob) -> None:
        async with self._sessions() as session, session.begin():
            await session.execute(
                update(Job)
                .where(*_still_ours(job))
                .values(status=JobStatus.DONE, locked_until=None, error=None)
            )

    async def fail(self, job: ClaimedJob, exc: BaseException) -> JobStatus:
        """Retry with exponential backoff; after `max_attempts` the job stays `failed` (visible)."""
        async with self._sessions() as session, session.begin():
            current = await session.scalar(select(Job).where(*_still_ours(job)))
            if current is None:  # reclaimed by another worker after our lease ran out
                return JobStatus.RUNNING
            current.error = describe_error(exc)
            current.locked_until = None
            if current.attempts >= current.max_attempts:
                current.status = JobStatus.FAILED
            else:
                delay = min(self._backoff_base_s * 2 ** (current.attempts - 1), self._backoff_cap_s)
                current.status = JobStatus.PENDING
                current.run_after = utcnow() + timedelta(seconds=delay)
            return current.status


def _still_ours(job: ClaimedJob) -> tuple[Any, ...]:
    return (Job.id == job.id, Job.status == JobStatus.RUNNING, Job.attempts == job.attempts)
