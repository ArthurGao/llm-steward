import uuid
from typing import Literal

from pydantic import BaseModel, Field

from llm_steward.schemas.memories import MemoryOut


class ChatMessage(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str = Field(min_length=1, max_length=20_000)


class EpisodeCreate(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=100)
    user_id: str | None = Field(default=None, max_length=200)
    agent_id: str | None = Field(default=None, max_length=200)
    session_id: str | None = Field(default=None, max_length=200)
    mode: Literal["async", "sync"] = Field(
        default="async",
        description="async: buffer and extract later. sync: extract now and return the memories.",
    )


class BufferScope(BaseModel):
    user_id: str | None = Field(default=None, max_length=200)
    agent_id: str | None = Field(default=None, max_length=200)
    session_id: str | None = Field(default=None, max_length=200)


class EpisodeAccepted(BaseModel):
    episode_id: uuid.UUID
    status: Literal["buffered", "queued", "processed"]
    job_id: uuid.UUID | None = None
    memories: list[MemoryOut] = Field(default_factory=list)


class FlushResult(BaseModel):
    job_id: uuid.UUID | None
