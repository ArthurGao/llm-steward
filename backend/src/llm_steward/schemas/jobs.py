import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from llm_steward.models.enums import JobStatus, JobType


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    type: JobType
    status: JobStatus
    attempts: int
    max_attempts: int
    error: str | None
    run_after: datetime
    created_at: datetime
    updated_at: datetime
