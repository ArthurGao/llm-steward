from fastapi import APIRouter

from llm_steward.api.deps import Reader, ServicesDep
from llm_steward.providers.base import Scope
from llm_steward.schemas.memories import MemoryBrief, ProfileOut

router = APIRouter(tags=["memories"])


@router.get("/profile")
async def get_profile(user_id: str, principal: Reader, services: ServicesDep) -> ProfileOut:
    """A user's current profile values (key -> value) and the pinned memories they see: the same
    core memories `/v1/retrieve` always puts in the context."""
    p = await services.memories.profile(Scope(principal.namespace_id, user_id))
    return ProfileOut(
        user_id=user_id,
        values=p.values,
        pinned=[MemoryBrief.model_validate(m) for m in p.pinned],
    )
