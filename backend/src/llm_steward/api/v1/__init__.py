from fastapi import APIRouter

from llm_steward.api.v1 import (
    candidates,
    episodes,
    kb,
    memories,
    profile,
    retrieve,
    suggestions,
)

router = APIRouter(prefix="/v1")
router.include_router(memories.router)
router.include_router(episodes.router)
router.include_router(profile.router)
router.include_router(retrieve.router)
router.include_router(kb.router)
router.include_router(candidates.router)
router.include_router(suggestions.router)
