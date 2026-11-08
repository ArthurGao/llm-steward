"""Outbox queue on both dialects: claim, lease, retry with backoff, terminal failure."""

import asyncio
import uuid
from dataclasses import dataclass
from datetime import timedelta

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from llm_steward.models import Job
from llm_steward.models.base import utcnow
from llm_steward.models.enums import JobStatus, JobType
from llm_steward.services.job_queue import JobQueue, enqueue


@dataclass
class Q:
    sessions: async_sessionmaker[AsyncSession]
    queue: JobQueue
    ns: uuid.UUID

    async def add(self) -> uuid.UUID:
        async with self.sessions() as s, s.begin():
            job = enqueue(s, self.ns, JobType.MEMORY_UPSERT, {"memory_id": "x"})
            await s.flush()
            return job.id

    async def job(self, job_id: uuid.UUID) -> Job:
        async with self.sessions() as s:
            job = await s.get(Job, job_id)
            assert job is not None
            return job


@pytest.fixture
def q(sessions: async_sessionmaker[AsyncSession], namespace_id: uuid.UUID) -> Q:
    return Q(sessions, JobQueue(sessions, backoff_base_s=10), namespace_id)


async def test_claim_then_complete(q: Q) -> None:
    job_id = await q.add()
    claimed = await q.queue.claim()
    assert claimed is not None and claimed.id == job_id and claimed.attempts == 1
    assert await q.queue.claim() is None  # leased: nobody else gets it
    await q.queue.complete(claimed)
    assert (await q.job(job_id)).status == JobStatus.DONE


async def test_failure_backs_off_then_gives_up(q: Q) -> None:
    job_id = await q.add()
    claimed = await q.queue.claim()
    assert claimed is not None
    started = utcnow()
    assert await q.queue.fail(claimed, ValueError("boom " + "x" * 2000)) == JobStatus.PENDING
    job = await q.job(job_id)
    assert job.error is not None and job.error.startswith("ValueError: boom")
    assert len(job.error) == 500
    assert job.run_after >= started + timedelta(seconds=10)  # base backoff
    assert await q.queue.claim() is None  # not due yet

    async with q.sessions() as s, s.begin():  # last allowed attempt, due now
        await s.execute(update(Job).where(Job.id == job_id).values(run_after=utcnow(), attempts=7))
    last = await q.queue.claim()
    assert last is not None and last.attempts == 8
    assert await q.queue.fail(last, RuntimeError("still down")) == JobStatus.FAILED
    assert (await q.job(job_id)).status == JobStatus.FAILED
    assert await q.queue.claim() is None


async def test_an_expired_lease_is_claimable_again(q: Q) -> None:
    job_id = await q.add()
    first = await q.queue.claim()
    assert first is not None
    async with q.sessions() as s, s.begin():  # the worker died: its lease ran out
        await s.execute(
            update(Job).where(Job.id == job_id).values(locked_until=utcnow() - timedelta(seconds=1))
        )
    second = await q.queue.claim()
    assert second is not None and second.id == job_id and second.attempts == 2


async def test_concurrent_claims_never_share_a_job(q: Q) -> None:
    ids = {await q.add() for _ in range(5)}
    claims = await asyncio.gather(*(q.queue.claim() for _ in range(12)))
    won = [c.id for c in claims if c is not None]
    assert len(won) == len(set(won))  # no job handed out twice
    assert set(won) <= ids


async def test_a_stale_worker_cannot_settle_a_reclaimed_job(q: Q) -> None:
    """A's lease ran out and B reclaimed the job: A's late complete/fail must not touch it."""
    job_id = await q.add()
    first = await q.queue.claim()
    assert first is not None
    async with q.sessions() as s, s.begin():
        await s.execute(
            update(Job).where(Job.id == job_id).values(locked_until=utcnow() - timedelta(seconds=1))
        )
    second = await q.queue.claim()
    assert second is not None and second.attempts == 2

    await q.queue.complete(first)
    await q.queue.fail(first, RuntimeError("late"))
    job = await q.job(job_id)
    assert (job.status, job.error) == (JobStatus.RUNNING, None)  # still B's
    assert job.locked_until is not None and job.locked_until > utcnow()

    await q.queue.complete(second)
    assert (await q.job(job_id)).status == JobStatus.DONE


async def test_a_hanging_handler_fails_before_its_lease_runs_out(
    q: Q, monkeypatch: pytest.MonkeyPatch
) -> None:
    from llm_steward.services.worker import Worker

    class Hanging:
        async def handle(self, job: object) -> None:
            await asyncio.sleep(30)

    queue = JobQueue(q.sessions, lease=timedelta(seconds=0.2), backoff_base_s=10)
    job_id = await q.add()
    assert await Worker(queue, Hanging().handle).run_once()
    job = await q.job(job_id)
    assert job.status == JobStatus.PENDING
    assert job.error is not None and job.error.startswith("TimeoutError")
