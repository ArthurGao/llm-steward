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
    ) -> None:
        self._queue = queue
        self._dispatch = dispatch
        self._ticks = ticks
        self._idle_sleep_s = idle_sleep_s

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
            await self.run_ticks()
            try:
                busy = await self.run_once()
            except Exception:  # the queue itself failed (e.g. database down): keep the loop alive
                log.exception("worker: claiming a job failed")
                busy = False
            if not busy:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=self._idle_sleep_s)
                except TimeoutError:
                    pass
