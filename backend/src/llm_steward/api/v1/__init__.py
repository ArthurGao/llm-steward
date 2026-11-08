from fastapi import APIRouter

from llm_steward.api.v1 import episodes, memories, profile, retrieve

router = APIRouter(prefix="/v1")
router.include_router(memories.router)
router.include_router(episodes.router)
router.include_router(profile.router)
router.include_router(retrieve.router)
