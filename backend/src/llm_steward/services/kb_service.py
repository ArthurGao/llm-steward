"""Knowledge: writes are submitted to GovernanceService, which screens them (rules, personal data)
and routes them by risk (design §5); reads come from the governance tables."""

import uuid
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from llm_steward.models import Candidate, KbCollection, KbEntry
from llm_steward.models.enums import (
    AuthorType,
    CandidateStage,
    CandidateStatus,
    KbOrigin,
    KbStatus,
    RiskLevel,
)
from llm_steward.services.governance_service import (
    Actor,
    DecideCandidate,
    GovernanceService,
    ReviseKbEntry,
    SubmitKbEntry,
    load_owned,
)
from llm_steward.services.paging import page


@dataclass(frozen=True)
class Submitted:
    """What a write became: live knowledge, or a candidate waiting for someone."""

    entry: KbEntry | None
    candidate: Candidate | None

    @classmethod
    def of(cls, obj: KbEntry | Candidate) -> "Submitted":
        return cls(obj, None) if isinstance(obj, KbEntry) else cls(None, obj)


@dataclass(frozen=True)
class EntryFilter:
    collection_id: uuid.UUID | None = None
    status: KbStatus | None = None


@dataclass(frozen=True)
class CandidateFilter:
    stage: CandidateStage | None = None
    status: CandidateStatus | None = CandidateStatus.PENDING
    user_id: str | None = None


class KbService:
    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], governance: GovernanceService
    ) -> None:
        self._sessions = sessions
        self._governance = governance

    async def submit(
        self,
        namespace_id: uuid.UUID,
        collection_id: uuid.UUID,
        title: str,
        content: str,
        *,
        actor: Actor,
        risk_level: RiskLevel = RiskLevel.LOW,
        user_id: str | None = None,
        tags: list[str] | None = None,
        origin: KbOrigin = KbOrigin.MANUAL,
        author_type: AuthorType = AuthorType.HUMAN,
    ) -> Submitted:
        """`risk_level` is the caller's own estimate: the rules can only raise it."""
        op = SubmitKbEntry(
            namespace_id=namespace_id,
            collection_id=collection_id,
            title=title,
            content=content,
            risk_level=risk_level,
            origin=origin,
            author_type=author_type,
            requested_for_user=user_id,
            tags=list(tags or []),
        )
        return Submitted.of(await self._governance.apply(op, actor))

    async def revise(
        self,
        namespace_id: uuid.UUID,
        entry_id: uuid.UUID,
        *,
        actor: Actor,
        title: str | None = None,
        content: str | None = None,
    ) -> Submitted:
        op = ReviseKbEntry(namespace_id, entry_id, title=title, content=content)
        return Submitted.of(await self._governance.apply(op, actor))

    async def list_collections(self, namespace_id: uuid.UUID) -> list[KbCollection]:
        async with self._sessions() as s:
            rows = await s.scalars(
                select(KbCollection)
                .where(KbCollection.namespace_id == namespace_id)
                .order_by(KbCollection.id)
            )
            return list(rows)

    async def get_entry(self, namespace_id: uuid.UUID, entry_id: uuid.UUID) -> KbEntry:
        async with self._sessions() as s:
            return await load_owned(s, KbEntry, namespace_id, entry_id, "KB entry", lock=False)

    async def list_entries(
        self, namespace_id: uuid.UUID, filters: EntryFilter, *, cursor: uuid.UUID | None, limit: int
    ) -> tuple[list[KbEntry], uuid.UUID | None]:
        query = select(KbEntry).where(KbEntry.namespace_id == namespace_id)
        if filters.collection_id is not None:
            query = query.where(KbEntry.collection_id == filters.collection_id)
        if filters.status is not None:
            query = query.where(KbEntry.status == filters.status)
        async with self._sessions() as s:
            return await page(s, query, KbEntry, cursor, limit)

    async def list_candidates(
        self,
        namespace_id: uuid.UUID,
        filters: CandidateFilter,
        *,
        cursor: uuid.UUID | None,
        limit: int,
    ) -> tuple[list[Candidate], uuid.UUID | None]:
        query = select(Candidate).where(Candidate.namespace_id == namespace_id)
        for column, value in (
            (Candidate.stage, filters.stage),
            (Candidate.status, filters.status),
            (Candidate.requested_for_user, filters.user_id),
        ):
            if value is not None:
                query = query.where(column == value)
        async with self._sessions() as s:
            return await page(s, query, Candidate, cursor, limit)

    async def decide(
        self,
        namespace_id: uuid.UUID,
        candidate_id: uuid.UUID,
        to: CandidateStatus,
        actor: Actor,
        *,
        draft: dict[str, Any] | None = None,
        scope: Literal["personal", "team"] = "team",
        note: str | None = None,
    ) -> tuple[Candidate, KbEntry | None]:
        """The decided candidate, and the knowledge it became (if it became any)."""
        op = DecideCandidate(namespace_id, candidate_id, to, note=note, draft=draft, scope=scope)
        candidate: Candidate = await self._governance.apply(op, actor)
        if candidate.status != CandidateStatus.ACCEPTED or candidate.target_id is None:
            return candidate, None
        async with self._sessions() as s:  # accepting points target_id at the knowledge
            return candidate, await s.get(KbEntry, candidate.target_id)
