"""Memories: writes go through GovernanceService; reads come from the governance mirror."""

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from llm_steward.errors import Unprocessable
from llm_steward.models import ExternalRef, Memory
from llm_steward.models.enums import MemoryStatus, ObjectType, SyncStatus
from llm_steward.providers.base import EXTRACT, MemoryProvider
from llm_steward.services.governance_service import (
    Actor,
    CreateMemory,
    DeleteMemory,
    GovernanceService,
    load_owned,
)
from llm_steward.storage.db import json_array_contains

MAX_PAGE = 200


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

    async def get(self, namespace_id: uuid.UUID, memory_id: uuid.UUID) -> MemoryView:
        async with self._sessions() as s:
            memory = await load_owned(s, Memory, namespace_id, memory_id, "memory", lock=False)
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
        if cursor is not None:
            query = query.where(Memory.id < cursor)
        query = query.order_by(Memory.id.desc()).limit(min(limit, MAX_PAGE) + 1)
        async with self._sessions() as s:
            rows = list(await s.scalars(query))
            page, more = rows[:limit], len(rows) > limit
            statuses = await self._sync_statuses(s, [m.id for m in page])
        views = [MemoryView(m, statuses.get(m.id, SyncStatus.PENDING)) for m in page]
        return views, (page[-1].id if more else None)

    async def delete(
        self, namespace_id: uuid.UUID, memory_id: uuid.UUID, *, actor: Actor
    ) -> MemoryView:
        memory: Memory = await self._governance.apply(DeleteMemory(namespace_id, memory_id), actor)
        return MemoryView(memory, SyncStatus.DELETING)  # its ref was marked in the same write

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
