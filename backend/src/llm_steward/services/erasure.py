"""Per-user erasure (design §7; the one exception to rule 6's "invalidate, never delete"): what
`EraseUser` deletes, as bulk statements in the governance write transaction (GovernanceService
dispatches it; nothing else calls this). Subqueries, not id lists: a heavy user's ids would exceed
SQLite's bound-parameter cap."""

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from llm_steward.models import (
    Candidate,
    ChangeLog,
    Episode,
    ExternalRef,
    IdempotencyKey,
    Job,
    Memory,
    RetrievalLog,
)
from llm_steward.models.enums import CandidateStatus, JobStatus, JobType, ObjectType, SyncStatus
from llm_steward.services.job_queue import enqueue


@dataclass(frozen=True)
class Erased:
    memories: int
    episodes: int
    candidates: int
    retrieval_logs: int


async def erase_user(s: AsyncSession, namespace_id: uuid.UUID, user_id: str) -> Erased:
    ns, user = namespace_id, user_id
    mine = (Memory.namespace_id == ns, Memory.user_id == user)
    memory_ids = select(Memory.id).where(*mine)
    episode_ids = select(Episode.id).where(Episode.namespace_id == ns, Episode.user_id == user)
    open_cards = (
        Candidate.namespace_id == ns,
        Candidate.status == CandidateStatus.PENDING,
        or_(Candidate.requested_for_user == user, Candidate.source_episode_id.in_(episode_ids)),
    )
    copies = await s.execute(
        delete(ExternalRef)
        .where(
            ExternalRef.namespace_id == ns,
            ExternalRef.object_type == ObjectType.MEMORY,
            ExternalRef.object_id.in_(memory_ids),
        )
        .returning(ExternalRef.object_id, ExternalRef.external_id, ExternalRef.sync_status)
    )
    for memory_id, external_id, status in copies.all():
        if external_id is not None and status != SyncStatus.DELETED:  # an engine copy
            payload = {"memory_id": str(memory_id), "external_id": external_id}
            enqueue(s, ns, JobType.MEMORY_REMOVE, payload)
    await s.execute(
        delete(ChangeLog).where(
            ChangeLog.namespace_id == ns,
            or_(
                ChangeLog.object_id.in_(memory_ids),
                ChangeLog.object_id.in_(episode_ids),
                ChangeLog.object_id.in_(select(Candidate.id).where(*open_cards)),
            ),
        )
    )
    deleted = await s.scalars(delete(Candidate).where(*open_cards).returning(Candidate.id))
    cards = [str(i) for i in deleted]
    # webhook events not yet delivered that name the user or one of their cards (Codex P5)
    await s.execute(
        delete(Job).where(
            Job.namespace_id == ns,
            Job.type == JobType.WEBHOOK_DELIVER,
            Job.status != JobStatus.DONE,
            or_(
                Job.payload[("data", "user_id")].as_string() == user,
                Job.payload[("data", "candidate_id")].as_string().in_(cards),
            ),
        )
    )
    # what stays must not point at what goes
    await s.execute(
        update(Memory)
        .where(
            Memory.namespace_id == ns,
            Memory.id.not_in(memory_ids),
            Memory.superseded_by.in_(memory_ids),
        )
        .values(superseded_by=None)
    )
    for model in (Memory, Candidate):
        columns: Any = model
        await s.execute(
            update(model)
            .where(columns.namespace_id == ns, columns.source_episode_id.in_(episode_ids))
            .values(source_episode_id=None)
        )
    memories = await s.execute(delete(Memory).where(*mine).returning(Memory.id))
    episodes = await s.execute(
        delete(Episode)
        .where(Episode.namespace_id == ns, Episode.user_id == user)
        .returning(Episode.id)
    )
    # Stored write responses (`Idempotency-Key`, kept a day) can carry the user's content, and
    # an API key's write cannot be tied to the user it was for: forget the namespace's.
    await s.execute(delete(IdempotencyKey).where(IdempotencyKey.namespace_id == ns))
    logs = await s.execute(
        delete(RetrievalLog)
        .where(
            RetrievalLog.namespace_id == ns,
            RetrievalLog.filters["user_id"].as_string() == user,
        )
        .returning(RetrievalLog.id)
    )
    return Erased(
        memories=len(memories.all()),
        episodes=len(episodes.all()),
        candidates=len(cards),
        retrieval_logs=len(logs.all()),
    )
