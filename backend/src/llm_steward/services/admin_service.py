"""What the Admin reads that `/v1` does not offer (design §9): counts, per-user rollups, the change
log, retrieval logs, jobs, sync refs and API keys, each inside one namespace. Read-only, except
the operator actions that only touch the outbox or keys (requeue a job, cancel it, requeue a sync):
governed writes still go through GovernanceService (the API layer calls it as the admin).
"""

import asyncio
import uuid
from collections import Counter
from collections.abc import Awaitable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from llm_steward.errors import Conflict
from llm_steward.models import (
    ApiKey,
    Candidate,
    ChangeLog,
    ExternalRef,
    Job,
    KbCollection,
    KbDocument,
    KbEntry,
    Memory,
    Namespace,
    RetrievalLog,
)
from llm_steward.models.base import utcnow
from llm_steward.models.enums import (
    ActorType,
    CandidateStage,
    CandidateStatus,
    JobStatus,
    JobType,
    KbStatus,
    MemoryStatus,
    ObjectType,
    SyncStatus,
)
from llm_steward.providers.base import Providers
from llm_steward.services.governance_service import load_owned
from llm_steward.services.health_service import HealthReport
from llm_steward.services.job_queue import enqueue, requeued
from llm_steward.services.paging import page

WEEK = 7
SYNC_JOBS = {
    ObjectType.MEMORY: (JobType.MEMORY_UPSERT, "memory_id"),
    ObjectType.KB_ENTRY: (JobType.KB_UPSERT, "entry_id"),
    ObjectType.KB_DOCUMENT: (JobType.DOC_INGEST, "document_id"),
}


@dataclass(frozen=True)
class DayCount:
    day: str  # YYYY-MM-DD (UTC)
    count: int


@dataclass(frozen=True)
class Gap:
    query: str  # as logged (personal data redacted)
    count: int


@dataclass(frozen=True)
class Stats:
    memories: dict[str, int]
    kb_entries: dict[str, int]
    documents: int
    pending_reviews: int
    awaiting_user: int
    writes_7d: list[DayCount]
    retrievals_7d: list[DayCount]
    retrieval_p95_ms: int | None
    failed_jobs: int
    gaps: list[Gap]


@dataclass(frozen=True)
class UserSummary:
    user_id: str
    memory_count: int
    pinned_count: int
    last_active_at: datetime


@dataclass(frozen=True)
class CollectionCounts:
    collection: KbCollection
    entry_count: int
    document_count: int


@dataclass(frozen=True)
class ProviderStatus:
    role: str
    name: str
    capabilities: list[str]
    healthy: bool
    detail: str | None
    pending: int
    failed: int
    last_reconciled_at: datetime | None


@dataclass(frozen=True)
class ActivityFilter:
    object_type: ObjectType | None = None
    object_id: uuid.UUID | None = None
    actor_type: ActorType | None = None


def is_gap(log: RetrievalLog) -> bool:
    """A retrieval that returned nothing: knowledge the KB is missing (design §9 "KB gaps")."""
    return log.hit_count == 0


class AdminService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def namespaces(self) -> list[Namespace]:
        async with self._sessions() as s:
            return list(await s.scalars(select(Namespace).order_by(Namespace.slug)))

    # --- dashboard ---------------------------------------------------------------------------

    async def stats(self, namespace_id: uuid.UUID) -> Stats:
        since = utcnow() - timedelta(days=WEEK)
        async with self._sessions() as s:
            memories = await _counts(s, Memory.status, Memory.namespace_id == namespace_id)
            entries = await _counts(s, KbEntry.status, KbEntry.namespace_id == namespace_id)
            documents = await _count(s, KbDocument, KbDocument.namespace_id == namespace_id)
            pending = (Candidate.namespace_id == namespace_id) & (
                Candidate.status == CandidateStatus.PENDING
            )
            reviews = await _count(
                s, Candidate, pending & (Candidate.stage == CandidateStage.AWAITING_REVIEW)
            )
            cards = await _count(
                s, Candidate, pending & (Candidate.stage == CandidateStage.AWAITING_USER)
            )
            failed = await _count(
                s, Job, (Job.namespace_id == namespace_id) & (Job.status == JobStatus.FAILED)
            )
            since_where = (ChangeLog.namespace_id == namespace_id) & (ChangeLog.created_at >= since)
            writes = list(await s.scalars(select(ChangeLog.created_at).where(since_where)))
            in_week = (RetrievalLog.namespace_id == namespace_id) & (
                RetrievalLog.created_at >= since
            )
            logs = (
                await s.execute(
                    select(RetrievalLog.created_at, RetrievalLog.latency_ms).where(in_week)
                )
            ).all()
            gaps = (
                await s.execute(
                    select(RetrievalLog.query, func.count())
                    .where(in_week, RetrievalLog.hit_count == 0)
                    .group_by(RetrievalLog.query)
                    .order_by(func.count().desc(), RetrievalLog.query)
                    .limit(10)
                )
            ).all()
        # per day in Python: a SQL date() of a PostgreSQL timestamptz uses the server's time zone
        latencies = sorted(int((latency or {}).get("total", 0)) for _, latency in logs)
        p95 = latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))] if latencies else None
        return Stats(
            memories={status.value: memories.get(status, 0) for status in MemoryStatus},
            kb_entries={status.value: entries.get(status, 0) for status in KbStatus},
            documents=documents,
            pending_reviews=reviews,
            awaiting_user=cards,
            writes_7d=_per_day(writes),
            retrievals_7d=_per_day([created for created, _ in logs]),
            retrieval_p95_ms=p95,
            failed_jobs=failed,
            gaps=[Gap(query, int(count)) for query, count in gaps],
        )

    # --- users -------------------------------------------------------------------------------

    async def users(
        self,
        namespace_id: uuid.UUID,
        *,
        after: tuple[datetime, str] | None = None,
        limit: int = 100,
    ) -> tuple[list[UserSummary], tuple[datetime, str] | None]:
        """End users with memories here (any status), most recently active first (ties by id).
        Keyset paging on (last activity, user id): `after` is the last row of the previous page."""
        found = await self._summaries(Memory.namespace_id == namespace_id, limit + 1, after)
        more = len(found) > limit
        rows = found[:limit]
        return rows, ((rows[-1].last_active_at, rows[-1].user_id) if more else None)

    async def user(self, namespace_id: uuid.UUID, user_id: str) -> UserSummary | None:
        found = await self._summaries(
            (Memory.namespace_id == namespace_id) & (Memory.user_id == user_id), 1
        )
        return found[0] if found else None

    async def _summaries(
        self, where: Any, limit: int, after: tuple[datetime, str] | None = None
    ) -> list[UserSummary]:
        pinned = case(
            (Memory.pinned.is_(True) & (Memory.status == MemoryStatus.ACTIVE), 1), else_=0
        )
        last = func.max(Memory.updated_at)
        query = (
            select(Memory.user_id, func.count(), func.sum(pinned), last)
            .where(where, Memory.user_id.is_not(None))
            .group_by(Memory.user_id)
            .order_by(last.desc(), Memory.user_id.desc())
            .limit(limit)
        )
        if after is not None:
            at, user = after
            query = query.having(or_(last < at, (last == at) & (Memory.user_id < user)))
        async with self._sessions() as s:
            rows = await s.execute(query)
            return [
                UserSummary(str(user), int(count), int(pins or 0), when)
                for user, count, pins, when in rows.all()
            ]

    # --- knowledge ---------------------------------------------------------------------------

    async def collections(self, namespace_id: uuid.UUID) -> list[CollectionCounts]:
        async with self._sessions() as s:
            collections = list(
                await s.scalars(
                    select(KbCollection)
                    .where(KbCollection.namespace_id == namespace_id)
                    .order_by(KbCollection.name)
                )
            )
            entries = await _counts(s, KbEntry.collection_id, KbEntry.namespace_id == namespace_id)
            docs = await _counts(
                s, KbDocument.collection_id, KbDocument.namespace_id == namespace_id
            )
        return [CollectionCounts(c, entries.get(c.id, 0), docs.get(c.id, 0)) for c in collections]

    async def entry_history(
        self, namespace_id: uuid.UUID, entry_id: uuid.UUID
    ) -> tuple[KbEntry, list[ChangeLog], list[KbEntry], KbEntry | None]:
        """The entry, its changes (oldest first), the entries it obsoletes and the one that
        obsoletes it (`superseded_by`)."""
        async with self._sessions() as s:
            entry = await load_owned(s, KbEntry, namespace_id, entry_id, "KB entry", lock=False)
            changes = list(
                await s.scalars(
                    select(ChangeLog)
                    .where(
                        ChangeLog.namespace_id == namespace_id,
                        ChangeLog.object_type == ObjectType.KB_ENTRY,
                        ChangeLog.object_id == entry_id,
                    )
                    .order_by(ChangeLog.id)
                )
            )
            obsoletes = list(
                await s.scalars(
                    select(KbEntry).where(
                        KbEntry.namespace_id == namespace_id, KbEntry.superseded_by == entry_id
                    )
                )
            )
            by = await s.get(KbEntry, entry.superseded_by) if entry.superseded_by else None
        return entry, changes, obsoletes, by

    # --- logs --------------------------------------------------------------------------------

    async def activity(
        self,
        namespace_id: uuid.UUID,
        filters: ActivityFilter,
        *,
        cursor: uuid.UUID | None,
        limit: int,
    ) -> tuple[list[ChangeLog], uuid.UUID | None]:
        query = select(ChangeLog).where(ChangeLog.namespace_id == namespace_id)
        for column, value in (
            (ChangeLog.object_type, filters.object_type),
            (ChangeLog.object_id, filters.object_id),
            (ChangeLog.actor_type, filters.actor_type),
        ):
            if value is not None:
                query = query.where(column == value)
        async with self._sessions() as s:
            return await page(s, query, ChangeLog, cursor, limit)

    async def retrieval_logs(
        self,
        namespace_id: uuid.UUID,
        *,
        gaps_only: bool = False,
        cursor: uuid.UUID | None,
        limit: int,
    ) -> tuple[list[RetrievalLog], uuid.UUID | None]:
        query = select(RetrievalLog).where(RetrievalLog.namespace_id == namespace_id)
        if gaps_only:
            query = query.where(RetrievalLog.hit_count == 0)
        async with self._sessions() as s:
            return await page(s, query, RetrievalLog, cursor, limit)

    async def retrieval_log(self, namespace_id: uuid.UUID, log_id: uuid.UUID) -> RetrievalLog:
        async with self._sessions() as s:
            return await load_owned(s, RetrievalLog, namespace_id, log_id, "retrieval", lock=False)

    # --- jobs and sync -----------------------------------------------------------------------

    async def jobs(
        self,
        namespace_id: uuid.UUID,
        *,
        status: JobStatus | None,
        job_type: JobType | None,
        cursor: uuid.UUID | None,
        limit: int,
    ) -> tuple[list[Job], uuid.UUID | None]:
        query = select(Job).where(Job.namespace_id == namespace_id)
        if status is not None:
            query = query.where(Job.status == status)
        if job_type is not None:
            query = query.where(Job.type == job_type)
        async with self._sessions() as s:
            return await page(s, query, Job, cursor, limit)

    async def retry_job(self, namespace_id: uuid.UUID, job_id: uuid.UUID) -> Job:
        """A failed job, given a fresh set of attempts (handlers are idempotent)."""
        return await self._set_job(namespace_id, job_id, JobStatus.FAILED, **requeued())

    async def cancel_job(self, namespace_id: uuid.UUID, job_id: uuid.UUID) -> Job:
        """A job not started yet, set aside as failed (visible, and retryable later)."""
        return await self._set_job(
            namespace_id, job_id, JobStatus.PENDING, status=JobStatus.FAILED, error="cancelled"
        )

    async def _set_job(
        self, namespace_id: uuid.UUID, job_id: uuid.UUID, expected: JobStatus, **values: Any
    ) -> Job:
        async with self._sessions() as s, s.begin():
            job = await load_owned(s, Job, namespace_id, job_id, "job")
            if job.status != expected:
                raise Conflict(f"a {job.status.value!r} job cannot be changed this way")
            for name, value in values.items():
                setattr(job, name, value)
            await s.flush()
            return job

    async def sync_refs(
        self,
        namespace_id: uuid.UUID,
        *,
        status: SyncStatus | None,
        cursor: uuid.UUID | None,
        limit: int,
    ) -> tuple[list[ExternalRef], uuid.UUID | None]:
        query = select(ExternalRef).where(ExternalRef.namespace_id == namespace_id)
        if status is not None:
            query = query.where(ExternalRef.sync_status == status)
        async with self._sessions() as s:
            return await page(s, query, ExternalRef, cursor, limit)

    async def retry_sync(self, namespace_id: uuid.UUID, ref_id: uuid.UUID) -> ExternalRef:
        """Push a failed object again: an outbox job (the Syncer still decides; a retired object
        is taken out instead)."""
        async with self._sessions() as s, s.begin():
            ref = await load_owned(s, ExternalRef, namespace_id, ref_id, "sync ref")
            if ref.sync_status != SyncStatus.FAILED:
                raise Conflict(
                    f"only a failed sync is retried (this one is {ref.sync_status.value!r})"
                )
            job_type, key = SYNC_JOBS[ref.object_type]
            ref.sync_status, ref.last_error = SyncStatus.PENDING, None
            enqueue(s, namespace_id, job_type, {key: str(ref.object_id)})
            await s.flush()
            return ref

    async def _sync_state(
        self, namespace_id: uuid.UUID
    ) -> tuple[dict[str, dict[str, int]], datetime | None]:
        """Pending / failed sync refs per provider, and when the memory engine was last
        reconciled against the mirror."""
        async with self._sessions() as s:
            rows = await s.execute(
                select(ExternalRef.provider, ExternalRef.sync_status, func.count())
                .where(
                    ExternalRef.namespace_id == namespace_id,
                    ExternalRef.sync_status.in_((SyncStatus.PENDING, SyncStatus.FAILED)),
                )
                .group_by(ExternalRef.provider, ExternalRef.sync_status)
            )
            reconciled = await s.scalar(
                select(func.max(Job.updated_at)).where(
                    Job.namespace_id == namespace_id,
                    Job.type == JobType.MEMORY_RECONCILE,
                    Job.status == JobStatus.DONE,
                )
            )
        counts: dict[str, dict[str, int]] = {}
        for provider, status, count in rows.all():
            counts.setdefault(provider, {"pending": 0, "failed": 0})[status.value] = int(count)
        return counts, reconciled

    async def providers(
        self, namespace_id: uuid.UUID, providers: Providers, probe: Awaitable[HealthReport]
    ) -> list[ProviderStatus]:
        """Each engine's health (`probe`, which calls the engines), its sync queue here and, for
        the memory engine, when it was last reconciled. The probe and the reads run together."""
        report, (queues, reconciled) = await asyncio.gather(probe, self._sync_state(namespace_id))
        statuses = []
        engines: tuple[tuple[str, str, Any], ...] = (
            ("memory", "memory_provider", providers.memory),
            ("kb", "kb_provider", providers.kb),
        )
        for role, key, provider in engines:
            health = report.components.get(key)
            queue = queues.get(provider.name, {"pending": 0, "failed": 0})
            statuses.append(
                ProviderStatus(
                    role=role,
                    name=provider.name,
                    capabilities=sorted(provider.capabilities),
                    healthy=health is None or health.status == "ok",
                    detail=health.detail if health else None,
                    pending=queue["pending"],
                    failed=queue["failed"],
                    last_reconciled_at=reconciled if role == "memory" else None,
                )
            )
        return statuses

    # --- keys --------------------------------------------------------------------------------

    async def keys(self, namespace_id: uuid.UUID) -> list[ApiKey]:
        async with self._sessions() as s:
            return list(
                await s.scalars(
                    select(ApiKey).where(ApiKey.namespace_id == namespace_id).order_by(ApiKey.id)
                )
            )

    async def revoke_key(self, namespace_id: uuid.UUID, key_id: uuid.UUID) -> ApiKey:
        async with self._sessions() as s, s.begin():
            key = await load_owned(s, ApiKey, namespace_id, key_id, "API key")
            key.revoked_at = key.revoked_at or utcnow()
            await s.flush()
            return key


async def _counts(s: AsyncSession, column: Any, where: Any) -> dict[Any, int]:
    rows = await s.execute(select(column, func.count()).where(where).group_by(column))
    return {value: int(count) for value, count in rows.all()}


async def _count(s: AsyncSession, model: Any, where: Any) -> int:
    return int(await s.scalar(select(func.count()).select_from(model).where(where)) or 0)


def _per_day(moments: list[datetime]) -> list[DayCount]:
    today = utcnow().date()
    days = [today - timedelta(days=offset) for offset in range(WEEK - 1, -1, -1)]
    counted = Counter(moment.date() for moment in moments)
    return [DayCount(day.isoformat(), counted.get(day, 0)) for day in days]
