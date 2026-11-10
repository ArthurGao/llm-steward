import uuid
from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Query, Response

from llm_steward.api.deps import OwnReader, OwnWriter, ServicesDep, Writer
from llm_steward.api.v1.kb import written
from llm_steward.models.enums import MemoryStatus
from llm_steward.schemas.kb import PromoteIn, Written
from llm_steward.schemas.memories import (
    ChangeOut,
    ErasedOut,
    MemoryBrief,
    MemoryCreate,
    MemoryHistoryOut,
    MemoryOut,
    MemoryPage,
    MemoryPatch,
)
from llm_steward.services.governance_service import CreateMemory, PatchMemory
from llm_steward.services.memory_service import MemoryFilter
from llm_steward.services.paging import MAX_PAGE

router = APIRouter(prefix="/memories", tags=["memories"])


@router.post("", status_code=201)
async def create_memory(
    body: MemoryCreate, principal: OwnWriter, services: ServicesDep
) -> MemoryOut:
    op = CreateMemory(
        namespace_id=principal.namespace_id,
        content=body.content,
        user_id=principal.pin_user(body.user_id),
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
    principal: OwnReader,
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
    user_id = principal.pin_user(user_id)
    filters = MemoryFilter(user_id, agent_id, session_id, memory_type, status, tag)
    views, next_cursor = await services.memories.list_memories(
        principal.namespace_id, filters, cursor=cursor, limit=limit
    )
    return MemoryPage(items=[MemoryOut.of(v) for v in views], next_cursor=next_cursor)


@router.delete("")
async def erase_user(
    principal: Writer, services: ServicesDep, user_id: Annotated[str, Query(min_length=1)]
) -> ErasedOut:
    """Erase everything llm-steward holds about one end user (GDPR-style; the one exception to
    "invalidate, never delete"): their memories and those memories' history, their episodes,
    their open cards and their retrieval logs. Engine copies are removed by the outbox. Team
    knowledge they helped decide stays. API keys only."""
    erased = await services.memories.erase_user(
        principal.namespace_id, user_id, actor=principal.actor
    )
    return ErasedOut(user_id=user_id, **asdict(erased))


@router.get("/{memory_id}")
async def get_memory(
    memory_id: uuid.UUID, principal: OwnReader, services: ServicesDep
) -> MemoryOut:
    view = await services.memories.get(principal.namespace_id, memory_id, owner=principal.user_id)
    return MemoryOut.of(view)


@router.delete("/{memory_id}")
async def delete_memory(
    memory_id: uuid.UUID, principal: OwnWriter, services: ServicesDep
) -> MemoryOut:
    """Soft delete: the memory becomes `deleted` and is removed from the provider by the Syncer."""
    view = await services.memories.delete(
        principal.namespace_id, memory_id, actor=principal.actor, owner=principal.user_id
    )
    return MemoryOut.of(view)


@router.patch("/{memory_id}")
async def patch_memory(
    memory_id: uuid.UUID, body: MemoryPatch, principal: OwnWriter, services: ServicesDep
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
        owner=principal.user_id,
    )
    return MemoryOut.of(await services.memories.patch(op, actor=principal.actor))


@router.get("/{memory_id}/history")
async def memory_history(
    memory_id: uuid.UUID, principal: OwnReader, services: ServicesDep
) -> MemoryHistoryOut:
    """Every change to this memory, and every version of the fact (the supersede chain)."""
    h = await services.memories.history(principal.namespace_id, memory_id, owner=principal.user_id)
    return MemoryHistoryOut(
        memory_id=memory_id,
        changes=[ChangeOut.model_validate(c) for c in h.changes],
        chain=[MemoryBrief.model_validate(m) for m in h.chain],
    )


@router.post("/{memory_id}/promote", status_code=201, responses={202: {"model": Written}})
async def promote(
    memory_id: uuid.UUID,
    body: PromoteIn,
    principal: Writer,
    services: ServicesDep,
    response: Response,
) -> Written:
    """Rewrite the memory as general knowledge (`kb.draft`), then route it like any KB write:
    201 live, 202 a candidate. Needs the KB LLM (STEWARD_KB_LLM)."""
    result = await services.kb.promote(
        principal.namespace_id,
        memory_id,
        body.collection_id,
        actor=principal.actor,
        user_id=body.user_id,
    )
    return written(result, response)
