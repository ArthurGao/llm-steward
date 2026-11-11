import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from llm_steward.models.enums import RESERVED_KEY_PREFIX, MemoryOrigin, MemoryStatus, SyncStatus
from llm_steward.schemas.bounds import Fact, MemoryType, UserId, Weight
from llm_steward.services.memory_service import MemoryView


class MemoryCreate(BaseModel):
    content: Fact
    user_id: UserId | None = None
    agent_id: str | None = Field(default=None, max_length=200)
    session_id: str | None = Field(default=None, max_length=200)
    memory_type: MemoryType = "semantic"
    key: str | None = Field(
        default=None,
        max_length=200,
        description=(
            "Profile key (e.g. user.city): one current value per user (and agent); a new value "
            "supersedes the old one. Not allowed together with session_id. Keys starting "
            "with `steward.` are llm-steward's own."
        ),
    )
    tags: list[str] = Field(default_factory=list, max_length=50)
    metadata: dict[str, Any] = Field(default_factory=dict)
    pinned: bool = False
    importance: Weight = 0.5
    confidence: Weight = 0.8
    infer: bool = Field(
        default=False,
        description="Let the memory provider extract facts. Needs the 'extract' capability (P2).",
    )

    @field_validator("key")
    @classmethod
    def _not_reserved(cls, key: str | None) -> str | None:
        if key is not None and key.startswith(RESERVED_KEY_PREFIX):
            raise ValueError(f"keys starting with {RESERVED_KEY_PREFIX!r} are llm-steward's own")
        return key


class SourceRef(BaseModel):
    type: str
    id: str


class MemoryOut(BaseModel):
    id: uuid.UUID
    content: str
    user_id: str | None
    agent_id: str | None
    session_id: str | None
    memory_type: str
    status: MemoryStatus
    origin: MemoryOrigin
    pinned: bool
    importance: float
    confidence: float
    tags: list[str]
    metadata: dict[str, Any]
    valid_from: datetime
    valid_to: datetime | None
    created_at: datetime
    updated_at: datetime
    sync_status: SyncStatus
    source_refs: list[SourceRef]

    @classmethod
    def of(cls, view: MemoryView) -> "MemoryOut":
        m = view.memory
        refs = (
            [SourceRef(type="episode", id=str(m.source_episode_id))] if m.source_episode_id else []
        )
        return cls(
            id=m.id,
            content=m.content,
            user_id=m.user_id,
            agent_id=m.agent_id,
            session_id=m.session_id,
            memory_type=m.memory_type,
            status=m.status,
            origin=m.origin,
            pinned=m.pinned,
            importance=m.importance,
            confidence=m.confidence,
            tags=m.tags,
            metadata=m.meta,
            valid_from=m.valid_from,
            valid_to=m.valid_to,
            created_at=m.created_at,
            updated_at=m.updated_at,
            sync_status=view.sync_status,
            source_refs=refs,
        )


class MemoryPage(BaseModel):
    items: list[MemoryOut]
    next_cursor: uuid.UUID | None


class MemoryPatch(BaseModel):
    pinned: bool | None = None
    importance: float | None = Field(default=None, ge=0, le=1)
    confidence: float | None = Field(default=None, ge=0, le=1)
    tags: list[str] | None = Field(default=None, max_length=50)
    metadata: dict[str, Any] | None = None


class MemoryBrief(BaseModel):
    """One version of a fact in a supersede chain."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    content: str
    status: MemoryStatus
    valid_from: datetime
    valid_to: datetime | None
    superseded_by: uuid.UUID | None


class ChangeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    op: str
    actor_type: str
    actor_id: str | None
    reason: str | None
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    created_at: datetime


class MemoryHistoryOut(BaseModel):
    memory_id: uuid.UUID
    changes: list[ChangeOut]
    chain: list[MemoryBrief]


class ProfileOut(BaseModel):
    user_id: str
    values: dict[str, str]  # profile key -> current value
    pinned: list[MemoryBrief]


class ErasedOut(BaseModel):
    """What one user's erasure removed (counts only)."""

    user_id: str
    memories: int
    episodes: int
    candidates: int
    retrieval_logs: int
