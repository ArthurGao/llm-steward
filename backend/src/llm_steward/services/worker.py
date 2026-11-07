"""Outbox worker: claim a job, hand it to the Syncer, record the outcome."""

import asyncio
import logging

from llm_steward.models.enums import JobStatus
from llm_steward.services.job_queue import JobQueue, describe_error
from llm_steward.services.sync_service import SyncService

log = logging.getLogger(__name__)


class Worker:
    def __init__(self, queue: JobQueue, sync: SyncService, *, idle_sleep_s: float = 1.0) -> None:
        self._queue = queue
        self._sync = sync
        self._idle_sleep_s = idle_sleep_s

    async def run_once(self) -> bool:
        """Process one claimable job; False when there was none.

        A job gets at most half its lease, so it fails (and backs off) before anyone else could
        reclaim it; a late finish could not settle the new claim anyway (see JobQueue)."""
        job = await self._queue.claim()
        if job is None:
            return False
        try:
            async with asyncio.timeout(self._queue.lease.total_seconds() / 2):
                await self._sync.handle(job)
        except Exception as exc:
            status = await self._queue.fail(job, exc)
            level = logging.ERROR if status == JobStatus.FAILED else logging.WARNING
            log.log(level, "job %s (%s) %s: %s", job.id, job.type, status, describe_error(exc))
        else:
            await self._queue.complete(job)
        return True

    async def drain(self, max_jobs: int = 10_000) -> int:
        """Run until nothing is claimable right now (jobs backing off are left for later)."""
        done = 0
        while done < max_jobs and await self.run_once():
            done += 1
        return done

    async def run_forever(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
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
