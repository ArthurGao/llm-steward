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


# Idle mode (a database that scales to zero, e.g. Neon): an idle worker stops querying; a request
# (touch) wakes it, a due job or the post-idle catch-up of the ticks wakes it on its own.


class CountingQueue(JobQueue):
    claims = 0

    async def claim(self, job_id: uuid.UUID | None = None):  # type: ignore[no-untyped-def]
        self.claims += 1
        return await super().claim(job_id)


async def test_next_due_reports_the_wait_until_a_job_is_claimable(q: Q) -> None:
    assert await q.queue.next_due_s() is None
    job_id = await q.add()
    async with q.sessions() as s, s.begin():
        await s.execute(
            update(Job).where(Job.id == job_id).values(run_after=utcnow() + timedelta(seconds=30))
        )
    due = await q.queue.next_due_s()
    assert due is not None and 25 < due <= 30
    await q.add()
    assert await q.queue.next_due_s() == 0


async def test_an_idle_worker_stops_querying_until_touched(q: Q) -> None:
    from llm_steward.services.worker import Worker

    queue = CountingQueue(q.sessions)
    ran: list[uuid.UUID] = []

    async def handle(job):  # type: ignore[no-untyped-def]
        ran.append(job.id)

    worker = Worker(queue, handle, idle_sleep_s=0.02, idle_after_s=0.1)
    stop = asyncio.Event()
    task = asyncio.create_task(worker.run_forever(stop))
    await asyncio.sleep(0.4)
    settled = queue.claims
    await asyncio.sleep(0.3)
    assert queue.claims == settled  # idle: no more claims
    job_id = await q.add()
    worker.touch()
    for _ in range(100):
        if ran:
            break
        await asyncio.sleep(0.01)
    stop.set()
    await asyncio.wait_for(task, timeout=2)
    assert ran == [job_id]


async def test_an_idle_worker_claims_a_job_when_it_falls_due(q: Q) -> None:
    # simplify review: idle, a job falling due must be claimed then, not asked about every second
    from llm_steward.services.worker import Worker

    class LookingQueue(CountingQueue):
        lookups = 0

        async def next_due_s(self) -> float | None:
            self.lookups += 1
            return await super().next_due_s()

    queue = LookingQueue(q.sessions)
    ran: list[uuid.UUID] = []

    async def handle(job):  # type: ignore[no-untyped-def]
        ran.append(job.id)

    worker = Worker(queue, handle, idle_sleep_s=0.02, idle_after_s=0.05)
    job_id = await q.add()
    async with q.sessions() as s, s.begin():
        await s.execute(
            update(Job).where(Job.id == job_id).values(run_after=utcnow() + timedelta(seconds=0.4))
        )
    stop = asyncio.Event()
    task = asyncio.create_task(worker.run_forever(stop))
    for _ in range(300):  # idle after 0.05 s; the job falls due at 0.4 s, nobody touches it
        if ran:
            break
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.5)  # then idle again: one look ahead (nothing waits), no polling
    stop.set()
    await asyncio.wait_for(task, timeout=2)
    assert ran == [job_id]
    assert queue.lookups <= 3


async def test_an_idle_worker_runs_its_ticks_once_after_the_catch_up_delay(q: Q) -> None:
    from llm_steward.services.worker import Tick, Worker

    calls: list[float] = []

    async def tick() -> None:
        calls.append(asyncio.get_running_loop().time())

    async def handle(job):  # type: ignore[no-untyped-def]
        return None

    worker = Worker(
        CountingQueue(q.sessions), handle, ticks=(Tick("flush", 0.01, tick),),
        idle_sleep_s=0.02, idle_after_s=0.05, idle_ticks_after_s=0.6,
    )  # fmt: skip
    stop = asyncio.Event()
    task = asyncio.create_task(worker.run_forever(stop))
    # Timeline from start: active until 0.05 s, idle after, catch-up at 0.6 s. Checks sit well
    # between those points so scheduling jitter cannot cross them.
    await asyncio.sleep(0.15)
    active_ticks = len(calls)
    assert active_ticks >= 1
    await asyncio.sleep(0.25)  # 0.4 s
    assert len(calls) == active_ticks  # idle, before the catch-up: no ticks
    await asyncio.sleep(0.5)  # 0.9 s
    assert len(calls) == active_ticks + 1  # the catch-up ran them once
    await asyncio.sleep(0.4)  # 1.3 s
    assert len(calls) == active_ticks + 1  # and not again until touched
    stop.set()
    await asyncio.wait_for(task, timeout=2)


async def test_without_idle_mode_the_worker_keeps_polling(q: Q) -> None:
    from llm_steward.services.worker import Worker

    queue = CountingQueue(q.sessions)

    async def handle(job):  # type: ignore[no-untyped-def]
        return None

    worker = Worker(queue, handle, idle_sleep_s=0.02)
    stop = asyncio.Event()
    task = asyncio.create_task(worker.run_forever(stop))
    await asyncio.sleep(0.3)
    stop.set()
    await asyncio.wait_for(task, timeout=2)
    assert queue.claims > 5


def test_a_request_wakes_an_idle_worker(migrated_url: str) -> None:
    import time

    from tests.conftest import running_app

    # Idle mode is for the worker inside the API process (requests wake it).
    with running_app(migrated_url, settings={"worker_idle_after_s": 0.05, "run_worker": True}) as c:
        worker = c.services.worker
        # Jobs other tests left in the schema keep it busy for a moment; wait until it is idle.
        deadline = time.monotonic() + 5
        while worker.active and time.monotonic() < deadline:
            time.sleep(0.02)
        assert not worker.active
        before = time.monotonic()
        c.http.get("/health")
        # The request touched it (not `worker.active`: that 50 ms window can close before a
        # loaded test machine returns the response — it failed once under the full suite).
        assert worker._last_active >= before


def test_the_catch_up_follows_the_episode_buffers_idle_time(migrated_url: str) -> None:
    from tests.conftest import running_app

    settings = {"worker_idle_after_s": 120, "buffer_idle_minutes": 30, "run_worker": True}
    with running_app(migrated_url, settings=settings) as c:
        assert c.services.worker._idle_ticks_after_s == 30 * 60 + 60


def test_a_separate_worker_process_never_goes_idle(migrated_url: str) -> None:
    from tests.conftest import running_app

    # `steward worker` alone gets no requests to wake it: it keeps polling.
    with running_app(
        migrated_url, settings={"worker_idle_after_s": 0.01, "run_worker": False}
    ) as c:
        assert c.services.worker._idle_after_s is None
