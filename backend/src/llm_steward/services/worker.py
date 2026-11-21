"""Outbox worker: claim a job, dispatch it by type, record the outcome; run periodic ticks."""

import asyncio
import contextlib
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from llm_steward.models.enums import JobStatus
from llm_steward.services.job_queue import ClaimedJob, JobQueue, describe_error
from llm_steward.storage.db import namespace_scope

log = logging.getLogger(__name__)

Dispatch = Callable[[ClaimedJob], Awaitable[Any]]


class JobNotClaimable(RuntimeError):
    """The job is already claimed (e.g. by the background worker), done or backing off."""

    def __init__(self, job_id: uuid.UUID) -> None:
        super().__init__(f"job {job_id} is not claimable")
        self.job_id = job_id


@dataclass
class Tick:
    """Periodic work (idle-buffer flush, daily reconcile); runs between jobs when due."""

    name: str
    every_s: float
    run: Callable[[], Awaitable[Any]]
    last: float = 0.0


class Worker:
    def __init__(
        self,
        queue: JobQueue,
        dispatch: Dispatch,
        *,
        ticks: tuple[Tick, ...] = (),
        idle_sleep_s: float = 1.0,
        idle_after_s: float | None = None,
        idle_ticks_after_s: float | None = None,
    ) -> None:
        """`idle_after_s` (a database that scales to zero, e.g. Neon): this long after the last
        `touch` (a request) or job, the loop stops querying. Idle, it sleeps until touched or the
        next job falls due, and runs the ticks once `idle_ticks_after_s` after going quiet (the
        episode buffers' idle flush). None: it polls every `idle_sleep_s`, as before."""
        self._queue = queue
        self._dispatch = dispatch
        self._ticks = ticks
        self._idle_sleep_s = idle_sleep_s
        self._idle_after_s = idle_after_s
        self._idle_ticks_after_s = idle_ticks_after_s
        self._wake = asyncio.Event()
        self.touch()

    def touch(self) -> None:
        """Activity: an idle loop wakes up and polls again, and the ticks' catch-up is re-armed."""
        self._last_active = time.monotonic()
        catch_up = self._idle_ticks_after_s
        self._ticks_at = None if catch_up is None else self._last_active + catch_up
        self._wake.set()
        self._wake = asyncio.Event()  # a waiter keeps the event it started with

    @property
    def active(self) -> bool:
        if self._idle_after_s is None:
            return True
        return time.monotonic() - self._last_active < self._idle_after_s

    async def run_once(self) -> bool:
        """Process one claimable job; False when there was none."""
        job = await self._queue.claim()
        if job is None:
            return False
        with contextlib.suppress(Exception):  # recorded on the job by _handle; the loop goes on
            await self._handle(job)
        return True

    async def run_job(self, job_id: uuid.UUID) -> Any:
        """Process one specific job now and return the handler's result (synchronous requests).
        A failure is recorded on the job (it backs off and retries later) and re-raised."""
        job = await self._queue.claim(job_id)
        if job is None:
            raise JobNotClaimable(job_id)
        return await self._handle(job)

    async def _handle(self, job: ClaimedJob) -> Any:
        """A job gets at most half its lease, so it fails (and backs off) before anyone else could
        reclaim it; a late finish could not settle the new claim anyway (see JobQueue)."""
        try:
            async with asyncio.timeout(self._queue.lease.total_seconds() / 2):
                with namespace_scope(job.namespace_id):  # the job's namespace only (P12)
                    result = await self._dispatch(job)
        except Exception as exc:
            status = await self._queue.fail(job, exc)
            level = logging.ERROR if status == JobStatus.FAILED else logging.WARNING
            log.log(level, "job %s (%s) %s: %s", job.id, job.type, status, describe_error(exc))
            raise
        await self._queue.complete(job)
        return result

    async def drain(self, max_jobs: int = 10_000) -> int:
        """Run until nothing is claimable right now (jobs backing off are left for later)."""
        done = 0
        while done < max_jobs and await self.run_once():
            done += 1
        return done

    async def run_ticks(self) -> None:
        now = time.monotonic()
        for tick in self._ticks:
            if now - tick.last >= tick.every_s:
                tick.last = now
                try:
                    await tick.run()
                except Exception:
                    log.exception("worker: tick %s failed", tick.name)

    async def run_forever(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            wake = self._wake  # taken first: a touch from here on ends the nap below
            if not self.active and not self._ticks_due():
                woken = await self._nap(wake, await self._idle_wait(), stop)
                if not woken and not self._ticks_due() and await self._claim():
                    self.touch()  # the nap ran out because a job fell due: claim it then
                continue
            if not self.active:
                self._ticks_at = None  # the catch-up runs once per quiet spell
            await self.run_ticks()
            if await self._claim():
                self.touch()
            else:
                await self._nap(wake, self._idle_sleep_s, stop)

    def _ticks_due(self) -> bool:
        return self._ticks_at is not None and time.monotonic() >= self._ticks_at

    async def _claim(self) -> bool:
        try:
            return await self.run_once()
        except Exception:  # the queue itself failed (e.g. database down): keep the loop alive
            log.exception("worker: claiming a job failed")
            return False

    async def _idle_wait(self) -> float | None:
        """Until the next job falls due or the ticks' catch-up, whichever is first (None: until
        touched)."""
        try:
            due = await self._queue.next_due_s()
        except Exception:
            log.exception("worker: looking ahead for jobs failed")
            due = self._idle_sleep_s
        waits = [] if due is None else [max(due, self._idle_sleep_s)]
        if self._ticks_at is not None:
            waits.append(max(self._ticks_at - time.monotonic(), 0.0))
        return min(waits) if waits else None

    @staticmethod
    async def _nap(wake: asyncio.Event, wait_s: float | None, stop: asyncio.Event) -> bool:
        """Sleep `wait_s` (None: until woken); True when touched or stopped, False on time out."""
        waits = {asyncio.ensure_future(wake.wait()), asyncio.ensure_future(stop.wait())}
        try:
            done, _ = await asyncio.wait(waits, timeout=wait_s, return_when=asyncio.FIRST_COMPLETED)
            return bool(done)
        finally:
            for w in waits:
                w.cancel()
            await asyncio.gather(*waits, return_exceptions=True)
