import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from llm_steward.api.deps import Reader, ServicesDep, Writer
from llm_steward.models.enums import MemoryStatus
from llm_steward.schemas.memories import (
    ChangeOut,
    MemoryBrief,
    MemoryCreate,
    MemoryHistoryOut,
    MemoryOut,
    MemoryPage,
    MemoryPatch,
)
from llm_steward.services.governance_service import CreateMemory, PatchMemory
from llm_steward.services.memory_service import MAX_PAGE, MemoryFilter

router = APIRouter(prefix="/memories", tags=["memories"])


@router.post("", status_code=201)
async def create_memory(body: MemoryCreate, principal: Writer, services: ServicesDep) -> MemoryOut:
    op = CreateMemory(
        namespace_id=principal.namespace_id,
        content=body.content,
        user_id=body.user_id,
        agent_id=body.agent_id,
        session_id=body.session_id,
        memory_type="profile" if body.key else body.memory_type,
        pinned=body.pinned,
        importance=body.importance,
        confidence=body.confidence,
        tags=body.tags,
        meta=body.metadata,
        key=body.key,
    )
    view = await services.memories.create(op, infer=body.infer, actor=principal.actor)
    return MemoryOut.of(view)


@router.get("")
async def list_memories(
    principal: Reader,
    services: ServicesDep,
    user_id: str | None = None,
    agent_id: str | None = None,
    session_id: str | None = None,
    memory_type: str | None = None,
    status: MemoryStatus | None = MemoryStatus.ACTIVE,
    tag: str | None = None,
    cursor: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 50,
) -> MemoryPage:
    filters = MemoryFilter(user_id, agent_id, session_id, memory_type, status, tag)
    views, next_cursor = await services.memories.list_memories(
        principal.namespace_id, filters, cursor=cursor, limit=limit
    )
    return MemoryPage(items=[MemoryOut.of(v) for v in views], next_cursor=next_cursor)


@router.get("/{memory_id}")
async def get_memory(memory_id: uuid.UUID, principal: Reader, services: ServicesDep) -> MemoryOut:
    return MemoryOut.of(await services.memories.get(principal.namespace_id, memory_id))


@router.delete("/{memory_id}")
async def delete_memory(
    memory_id: uuid.UUID, principal: Writer, services: ServicesDep
) -> MemoryOut:
    """Soft delete: the memory becomes `deleted` and is removed from the provider by the Syncer."""
    view = await services.memories.delete(principal.namespace_id, memory_id, actor=principal.actor)
    return MemoryOut.of(view)


@router.patch("/{memory_id}")
async def patch_memory(
    memory_id: uuid.UUID, body: MemoryPatch, principal: Writer, services: ServicesDep
) -> MemoryOut:
    """Pin, re-weight or re-tag. New content is a new memory (POST) that supersedes this one."""
    op = PatchMemory(
        principal.namespace_id,
        memory_id,
        pinned=body.pinned,
        importance=body.importance,
        confidence=body.confidence,
        tags=body.tags,
        meta=body.metadata,
    )
    return MemoryOut.of(await services.memories.patch(op, actor=principal.actor))


@router.get("/{memory_id}/history")
async def memory_history(
    memory_id: uuid.UUID, principal: Reader, services: ServicesDep
) -> MemoryHistoryOut:
    """Every change to this memory, and every version of the fact (the supersede chain)."""
    h = await services.memories.history(principal.namespace_id, memory_id)
    return MemoryHistoryOut(
        memory_id=memory_id,
        changes=[ChangeOut.model_validate(c) for c in h.changes],
        chain=[MemoryBrief.model_validate(m) for m in h.chain],
    )
