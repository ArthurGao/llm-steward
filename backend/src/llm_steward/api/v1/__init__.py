from fastapi import APIRouter

from llm_steward.api.v1 import memories, retrieve

router = APIRouter(prefix="/v1")
router.include_router(memories.router)
router.include_router(retrieve.router)
