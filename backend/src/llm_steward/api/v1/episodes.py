import asyncio

from fastapi import APIRouter, Response

from llm_steward.api.deps import ServicesDep, Writer
from llm_steward.providers.base import Scope
from llm_steward.schemas.episodes import BufferScope, EpisodeAccepted, EpisodeCreate, FlushResult
from llm_steward.schemas.kb import CandidateOut
from llm_steward.schemas.memories import MemoryOut

router = APIRouter(prefix="/episodes", tags=["episodes"])


@router.post("", status_code=202)
async def add_episode(
    body: EpisodeCreate, principal: Writer, services: ServicesDep, response: Response
) -> EpisodeAccepted:
    """Store conversation messages for memory extraction (design §5)."""
    scope = Scope(principal.namespace_id, body.user_id, body.agent_id, body.session_id)
    result = await services.episodes.ingest(
        scope,
        [m.model_dump() for m in body.messages],
        principal.actor,
        sync=body.mode == "sync",
    )
    memories = []
    if result.status == "processed":
        response.status_code = 200
        views, cards = await asyncio.gather(  # independent reads
            services.memories.get_many(principal.namespace_id, result.memory_ids),
            services.kb.cards(scope),
        )
        memories = [MemoryOut.of(v) for v in views]
    else:
        cards = await services.kb.cards(scope)
    return EpisodeAccepted(
        episode_id=result.episode.id,
        status=result.status,
        job_id=result.job_id,
        memories=memories,
        suggestions=[CandidateOut.model_validate(c) for c in cards],
    )


@router.post("/flush")
async def flush(body: BufferScope, principal: Writer, services: ServicesDep) -> FlushResult:
    """Extract this scope's buffered episodes now instead of waiting for size or idle time."""
    scope = Scope(principal.namespace_id, body.user_id, body.agent_id, body.session_id)
    return FlushResult(job_id=await services.episodes.flush(scope))
