"""Outbox on the `jobs` table: enqueue inside the writer's transaction, claim with a lease.

Claiming is a conditional UPDATE (`... WHERE id = :id AND <still claimable>`), so two workers can
never both win a job on either dialect; PostgreSQL additionally skips rows another claimer locked.
Every claim increments `attempts`, which doubles as the claim's fencing token: complete/fail only
apply while the job is still on the same claim, so a worker whose lease ran out (and whose job was
reclaimed) can no longer settle someone else's claim.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from llm_steward.errors import NotFound, StewardError
from llm_steward.models import Job
from llm_steward.models.base import utcnow
from llm_steward.models.enums import JobStatus, JobType
from llm_steward.pipeline.risk import redact_pii

ERROR_MAX_CHARS = 500


@dataclass(frozen=True)
class ClaimedJob:
    id: uuid.UUID
    namespace_id: uuid.UUID
    type: JobType
    payload: dict[str, Any]
    attempts: int
    max_attempts: int = 8

    @property
    def last_attempt(self) -> bool:
        """A handler that settles its own object on giving up (e.g. a failed document) asks."""
        return self.attempts >= self.max_attempts


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
    """Exception type plus a short message, as stored in jobs.error and external_refs.last_error.
    Our own errors are written without provider bodies; any other exception (a driver's, a
    library's) can echo what it was given, so personal data in it is redacted. Redacted before
    the cut, over a window larger than what is kept, so a value is never cut in half unredacted."""
    message = (
        str(exc) if isinstance(exc, StewardError) else redact_pii(str(exc)[: 4 * ERROR_MAX_CHARS])
    )
    return f"{type(exc).__name__}: {message}"[:ERROR_MAX_CHARS]


class PermanentFailure(StewardError):
    """A job failure retrying cannot fix (e.g. a webhook endpoint answering 410): the job is
    failed at once, visible to `steward jobs failed`, instead of backing off for nothing."""

    code = "permanent_failure"


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
                    .returning(
                        Job.id,
                        Job.namespace_id,
                        Job.type,
                        Job.payload,
                        Job.attempts,
                        Job.max_attempts,
                    )
                )
            ).first()
        if row is None:  # another worker won the race
            return None
        return ClaimedJob(
            row.id, row.namespace_id, JobType(row.type), row.payload, row.attempts, row.max_attempts
        )

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
            if current.attempts >= current.max_attempts or isinstance(exc, PermanentFailure):
                current.status = JobStatus.FAILED
            else:
                delay = min(self._backoff_base_s * 2 ** (current.attempts - 1), self._backoff_cap_s)
                current.status = JobStatus.PENDING
                current.run_after = utcnow() + timedelta(seconds=delay)
            return current.status

    async def get(self, namespace_id: uuid.UUID, job_id: uuid.UUID) -> Job:
        """One job's status (`GET /v1/jobs/{id}`); another namespace's is not found."""
        async with self._sessions() as session:
            job = await session.scalar(
                select(Job).where(Job.id == job_id, Job.namespace_id == namespace_id)
            )
        if job is None:
            raise NotFound(f"job {job_id} not found")
        return job

    async def failed(self, namespace_id: uuid.UUID | None = None, limit: int = 100) -> list[Job]:
        """Jobs that ran out of attempts, newest first (what an operator looks at)."""
        query = select(Job).where(Job.status == JobStatus.FAILED)
        if namespace_id is not None:
            query = query.where(Job.namespace_id == namespace_id)
        async with self._sessions() as session:
            return list(await session.scalars(query.order_by(Job.updated_at.desc()).limit(limit)))

    async def retry(self, job_ids: Sequence[uuid.UUID] | None = None) -> int:
        """Give failed jobs (those ids, or every failed job when None) a fresh set of attempts,
        due now. Handlers are idempotent, so a retried extraction resumes where it stopped.
        Only `failed` jobs are touched; returns how many were requeued."""
        query = update(Job).where(Job.status == JobStatus.FAILED)
        if job_ids is not None:
            query = query.where(Job.id.in_(job_ids))
        async with self._sessions() as session, session.begin():
            result = await session.execute(
                query.values(status=JobStatus.PENDING, attempts=0, run_after=utcnow())
                .execution_options(synchronize_session=False)
                .returning(Job.id)
            )
            return len(result.all())


def _still_ours(job: ClaimedJob) -> tuple[Any, ...]:
    return (Job.id == job.id, Job.status == JobStatus.RUNNING, Job.attempts == job.attempts)
