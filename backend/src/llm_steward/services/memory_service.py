"""Memories: writes go through GovernanceService; reads come from the governance mirror."""

import uuid
from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from llm_steward.errors import Unprocessable
from llm_steward.models import ChangeLog, ExternalRef, Memory
from llm_steward.models.enums import MemoryStatus, ObjectType, SyncStatus
from llm_steward.providers.base import EXTRACT, MemoryProvider, Scope
from llm_steward.services.governance_service import (
    Actor,
    CreateMemory,
    DeleteMemory,
    Erased,
    EraseUser,
    GovernanceService,
    PatchMemory,
    load_owned,
)
from llm_steward.services.paging import page
from llm_steward.services.scoping import visible
from llm_steward.storage.db import json_array_contains

CORE_MAX = 20


@dataclass(frozen=True)
class MemoryView:
    memory: Memory
    sync_status: SyncStatus


@dataclass(frozen=True)
class MemoryFilter:
    user_id: str | None = None
    agent_id: str | None = None
    session_id: str | None = None
    memory_type: str | None = None
    status: MemoryStatus | None = MemoryStatus.ACTIVE
    tag: str | None = None


@dataclass(frozen=True)
class MemoryHistory:
    changes: list[ChangeLog]  # this memory's change_log, oldest first
    chain: list[Memory]  # every version of this fact, oldest first (follows superseded_by)


@dataclass(frozen=True)
class Profile:
    values: dict[str, str]  # profile key -> current value
    pinned: list[Memory]


class MemoryService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        governance: GovernanceService,
        provider: MemoryProvider,
    ) -> None:
        self._sessions = sessions
        self._governance = governance
        self._provider = provider

    async def create(self, op: CreateMemory, *, infer: bool, actor: Actor) -> MemoryView:
        if infer and EXTRACT not in self._provider.capabilities:
            raise Unprocessable(
                f"the {self._provider.name} memory provider cannot extract memories; "
                "send infer=false (extraction arrives with mem0 in P2)"
            )
        memory: Memory = await self._governance.apply(op, actor)
        return MemoryView(memory, SyncStatus.PENDING)  # its ref was opened as pending

    async def get(
        self, namespace_id: uuid.UUID, memory_id: uuid.UUID, *, owner: str | None = None
    ) -> MemoryView:
        async with self._sessions() as s:
            memory = await load_owned(
                s, Memory, namespace_id, memory_id, "memory", lock=False, owner=owner
            )
            statuses = await self._sync_statuses(s, [memory.id])
        return MemoryView(memory, statuses.get(memory.id, SyncStatus.PENDING))

    async def list_memories(
        self,
        namespace_id: uuid.UUID,
        filters: MemoryFilter,
        *,
        cursor: uuid.UUID | None,
        limit: int,
    ) -> tuple[list[MemoryView], uuid.UUID | None]:
        """Newest first; `cursor` is the last id of the previous page (ids are UUIDv7)."""
        query = select(Memory).where(Memory.namespace_id == namespace_id)
        for column, value in (
            (Memory.user_id, filters.user_id),
            (Memory.agent_id, filters.agent_id),
            (Memory.session_id, filters.session_id),
            (Memory.memory_type, filters.memory_type),
            (Memory.status, filters.status),
        ):
            if value is not None:
                query = query.where(column == value)
        if filters.tag is not None:
            query = query.where(json_array_contains(Memory.tags, filters.tag))
        async with self._sessions() as s:
            memories: list[Memory]
            memories, next_cursor = await page(s, query, Memory, cursor, limit)
            statuses = await self._sync_statuses(s, [m.id for m in memories])
        views = [MemoryView(m, statuses.get(m.id, SyncStatus.PENDING)) for m in memories]
        return views, next_cursor

    async def get_many(
        self, namespace_id: uuid.UUID, memory_ids: list[uuid.UUID]
    ) -> list[MemoryView]:
        """In the order given; ids outside the namespace are left out."""
        async with self._sessions() as s:
            rows = await s.scalars(
                select(Memory).where(Memory.namespace_id == namespace_id, Memory.id.in_(memory_ids))
            )
            found = {m.id: m for m in rows}
            statuses = await self._sync_statuses(s, list(found))
        return [
            MemoryView(found[i], statuses.get(i, SyncStatus.PENDING))
            for i in memory_ids
            if i in found
        ]

    async def history(
        self, namespace_id: uuid.UUID, memory_id: uuid.UUID, *, owner: str | None = None
    ) -> MemoryHistory:
        async with self._sessions() as s:
            memory = await load_owned(
                s, Memory, namespace_id, memory_id, "memory", lock=False, owner=owner
            )
            changes = list(
                await s.scalars(
                    select(ChangeLog)
                    .where(
                        ChangeLog.object_type == ObjectType.MEMORY,
                        ChangeLog.object_id == memory_id,
                        ChangeLog.namespace_id == namespace_id,
                    )
                    .order_by(ChangeLog.id)
                )
            )
            chain = [memory]
            earlier = memory
            while True:  # walk back: the memory this one superseded, and so on
                previous = await s.scalar(
                    select(Memory).where(
                        Memory.superseded_by == earlier.id, Memory.namespace_id == namespace_id
                    )
                )
                if previous is None or previous in chain:
                    break
                chain.insert(0, previous)
                earlier = previous
            later = memory
            while later.superseded_by is not None:  # walk forward
                nxt = await s.get(Memory, later.superseded_by)
                if nxt is None or nxt in chain or nxt.namespace_id != namespace_id:
                    break
                chain.append(nxt)
                later = nxt
        return MemoryHistory(changes, chain)

    async def profile(self, scope: Scope) -> Profile:
        """The same core memories retrieval always puts in the context (see `core_memories`)."""
        async with self._sessions() as s:
            core = await core_memories(s, scope)
        # own values after shared ones, so a user's value wins over a shared default
        keyed = sorted((m for m in core if m.key), key=lambda m: m.user_id is not None)
        return Profile({m.key: m.content for m in keyed if m.key}, [m for m in core if m.pinned])

    async def patch(self, op: PatchMemory, *, actor: Actor) -> MemoryView:
        memory: Memory = await self._governance.apply(op, actor)
        return await self.get(memory.namespace_id, memory.id)

    async def delete(
        self,
        namespace_id: uuid.UUID,
        memory_id: uuid.UUID,
        *,
        actor: Actor,
        owner: str | None = None,
    ) -> MemoryView:
        op = DeleteMemory(namespace_id, memory_id, owner=owner)
        memory: Memory = await self._governance.apply(op, actor)
        return MemoryView(memory, SyncStatus.DELETING)  # its ref was marked in the same write

    async def erase_user(self, namespace_id: uuid.UUID, user_id: str, *, actor: Actor) -> Erased:
        """Everything held about one end user, deleted (see `EraseUser`)."""
        erased: Erased = await self._governance.apply(EraseUser(namespace_id, user_id), actor)
        return erased

    async def _sync_statuses(
        self, s: AsyncSession, ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, SyncStatus]:
        if not ids:
            return {}
        refs = await s.execute(
            select(ExternalRef.object_id, ExternalRef.sync_status).where(
                ExternalRef.object_type == ObjectType.MEMORY,
                ExternalRef.provider == self._provider.name,
                ExternalRef.object_id.in_(ids),
            )
        )
        return {object_id: SyncStatus(status) for object_id, status in refs}


async def core_memories(s: AsyncSession, scope: Scope) -> list[Memory]:
    """Active pinned memories and profile values this scope sees (own or shared), most important
    first: straight from the mirror, no provider call (design §6 step 8)."""
    rows = await s.scalars(
        select(Memory)
        .where(
            *visible(Memory, scope, session=False),
            Memory.status == MemoryStatus.ACTIVE,
            or_(Memory.pinned.is_(True), Memory.key.is_not(None)),
        )
        .order_by(Memory.importance.desc(), Memory.id)
        .limit(CORE_MAX)
    )
    return list(rows)
