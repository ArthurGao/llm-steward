import uuid

from fastapi import APIRouter

from llm_steward.api.deps import Reader, ServicesDep
from llm_steward.schemas.jobs import JobOut

router = APIRouter(tags=["system"])


@router.get("/jobs/{job_id}")
async def get_job(job_id: uuid.UUID, principal: Reader, services: ServicesDep) -> JobOut:
    """An outbox job's status (design §7): pending, running, done or failed, with its attempts and
    its last error (already redacted). Its payload is internal and not shown."""
    return JobOut.model_validate(await services.jobs.get(principal.namespace_id, job_id))
