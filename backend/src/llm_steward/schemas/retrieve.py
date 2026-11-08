import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RetrieveScope(BaseModel):
    user_id: str | None = None
    agent_id: str | None = None
    session_id: str | None = None


class RetrieveIn(BaseModel):
    query: str = Field(min_length=1, max_length=4_000)
    scope: RetrieveScope = Field(default_factory=RetrieveScope)
    sources: list[Literal["memory", "kb"]] = Field(default=["memory", "kb"], min_length=1)
    collections: list[str] | None = None
    top_k: int = Field(default=8, ge=1, le=50)
    format: Literal["items", "context"] = "items"
    as_of: datetime | None = Field(
        default=None, description="What was valid at this moment (superseded memories included)."
    )


class RetrievedItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    source: Literal["memory", "kb"]
    id: uuid.UUID
    content: str
    title: str | None
    status: str
    verified: bool
    score: float
    pinned: bool


class RetrieveOut(BaseModel):
    retrieval_id: uuid.UUID
    items: list[RetrievedItemOut]
    degraded: list[Literal["memory", "kb"]]
    context: str | None = None
