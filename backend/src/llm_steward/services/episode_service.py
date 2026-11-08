"""Conversation episodes and their buffer (design §5 step 1).

Episodes accumulate per scope (namespace, user, agent, session). A flush claims the unprocessed ones
with a conditional update — so two flushes (two requests, two replicas, a request and the idle tick)
can never extract the same episode twice — and queues one `memory.extract` job for the batch.
"""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Literal

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from llm_steward.errors import ProviderUnavailable, Unprocessable
from llm_steward.models import Episode
from llm_steward.models.base import utcnow
from llm_steward.models.enums import EpisodeKind, JobType
from llm_steward.providers.base import EXTRACT, MemoryProvider, Scope
from llm_steward.services.governance_service import Actor, CreateEpisode, GovernanceService
from llm_steward.services.job_queue import enqueue
from llm_steward.services.scoping import exact
from llm_steward.services.worker import JobNotClaimable

RunJob = Callable[[uuid.UUID], Awaitable[Any]]
Status = Literal["buffered", "queued", "processed"]


@dataclass(frozen=True)
class Ingested:
    episode: Episode
    status: Status
    job_id: uuid.UUID | None = None
    memory_ids: list[uuid.UUID] = field(default_factory=list)  # set when processed


def estimate_tokens(text: str) -> int:
    """Rough and cheap: a CJK character is about one token, other text about four characters."""
    cjk = sum(1 for ch in text if "㐀" <= ch <= "鿿")
    return cjk + (len(text) - cjk) // 4


class EpisodeService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        governance: GovernanceService,
        provider: MemoryProvider,
        run_job: RunJob,
        *,
        buffer_tokens: int,
        buffer_idle: timedelta,
    ) -> None:
        self._sessions = sessions
        self._governance = governance
        self._provider = provider
        self._run_job = run_job  # the worker's: a synchronous request still runs as a leased job
        self._buffer_tokens = buffer_tokens
        self._buffer_idle = buffer_idle

    async def ingest(
        self, scope: Scope, messages: list[dict[str, str]], actor: Actor, *, sync: bool
    ) -> Ingested:
        """Store an episode. Async: flush only when the buffer is large enough. Sync: flush now and
        run the extraction in this call; a failure leaves the job to retry with backoff."""
        if EXTRACT not in self._provider.capabilities:
            raise Unprocessable(
                f"the {self._provider.name} memory provider cannot extract memories from "
                "conversations; write memories directly with POST /v1/memories"
            )
        episode: Episode = await self._governance.apply(
            CreateEpisode(
                scope.namespace_id,
                EpisodeKind.CONVERSATION,
                messages,
                scope.user_id,
                scope.agent_id,
                scope.session_id,
            ),
            actor,
        )
        if not sync:
            full = await self._buffered_tokens(scope) >= self._buffer_tokens
            job_id = await self.flush(scope) if full else None
            return Ingested(episode, "queued" if job_id else "buffered", job_id)
        job_id = await self.flush(scope)
        if job_id is None:  # flushed concurrently by someone else: it is queued, not lost
            return Ingested(episode, "queued")
        try:
            memory_ids: list[uuid.UUID] = await self._run_job(job_id)
        except JobNotClaimable:  # the background worker got to it first: it is running there
            return Ingested(episode, "queued", job_id)
        except Exception as exc:  # recorded on the job, which retries; whatever the cause
            raise ProviderUnavailable(
                f"extraction failed ({type(exc).__name__}); job {job_id} will be retried"
            ) from exc
        return Ingested(episode, "processed", job_id, memory_ids)

    async def flush(self, scope: Scope) -> uuid.UUID | None:
        """Claim the buffer's unprocessed episodes and queue their extraction; None if empty."""
        async with self._sessions() as s, s.begin():
            claimed = list(
                await s.scalars(
                    update(Episode)
                    .where(*_unprocessed(scope))
                    .values(processed_at=utcnow())
                    .returning(Episode.id)
                )
            )
            if not claimed:
                return None
            job = enqueue(
                s,
                scope.namespace_id,
                JobType.MEMORY_EXTRACT,
                {"episode_ids": [str(i) for i in sorted(claimed)]},
            )
            await s.flush()
            return job.id

    async def flush_idle(self) -> int:
        """Flush every buffer whose newest episode is older than the idle limit."""
        cutoff = utcnow() - self._buffer_idle
        async with self._sessions() as s:
            rows = await s.execute(
                select(Episode.namespace_id, Episode.user_id, Episode.agent_id, Episode.session_id)
                .where(Episode.processed_at.is_(None))
                .group_by(
                    Episode.namespace_id, Episode.user_id, Episode.agent_id, Episode.session_id
                )
                .having(func.max(Episode.created_at) < cutoff)
            )
            scopes = [Scope(*row) for row in rows]
        flushed = 0
        for scope in scopes:
            flushed += await self.flush(scope) is not None
        return flushed

    async def _buffered_tokens(self, scope: Scope) -> int:
        async with self._sessions() as s:
            contents = await s.scalars(select(Episode.content).where(*_unprocessed(scope)))
            return sum(
                estimate_tokens(m.get("content", ""))
                for content in contents
                for m in content.get("messages", [])
            )


def _unprocessed(scope: Scope) -> tuple[Any, ...]:
    return (*exact(Episode, scope, session=True), Episode.processed_at.is_(None))
