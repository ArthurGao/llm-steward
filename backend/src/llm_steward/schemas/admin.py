"""Admin responses (`/admin/v1`, design §9) that `/v1` has no model for. Objects `/v1` already
returns (memories, entries, candidates, documents) use the `/v1` models."""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from llm_steward.models.enums import ActorType, ChangeOp, JobStatus, JobType, ObjectType, SyncStatus
from llm_steward.schemas.kb import CollectionOut
from llm_steward.schemas.memories import MemoryBrief


class NamespaceOut(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    memory_provider: str
    kb_provider: str


class NamespacePage(BaseModel):
    items: list[NamespaceOut]


class DayCountOut(BaseModel):
    day: str
    count: int


class GapOut(BaseModel):
    query: str
    count: int


class StatsOut(BaseModel):
    memories: dict[str, int]
    kb_entries: dict[str, int]
    documents: int
    pending_reviews: int
    awaiting_user: int
    writes_7d: list[DayCountOut]
    retrievals_7d: list[DayCountOut]
    retrieval_p95_ms: int | None
    failed_jobs: int
    gaps: list[GapOut]


class UserSummaryOut(BaseModel):
    user_id: str
    memory_count: int
    pinned_count: int
    last_active_at: datetime


class UserPage(BaseModel):
    items: list[UserSummaryOut]


class UserDetailOut(UserSummaryOut):
    profile: dict[str, str]
    pinned: list[MemoryBrief]


class CollectionCountsOut(CollectionOut):
    entry_count: int
    document_count: int


class CollectionCountsPage(BaseModel):
    items: list[CollectionCountsOut]


class EntryVersionOut(BaseModel):
    """One version of an entry: what its text was from `changed_at` on."""

    version: int
    title: str
    content: str
    status: str
    changed_at: datetime
    actor_type: ActorType
    actor_id: str | None
    reason: str | None


class EntryLink(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    title: str
    status: str
    version: int


class EntryHistoryOut(BaseModel):
    entry_id: uuid.UUID
    versions: list[EntryVersionOut]  # newest first
    obsoletes: list[EntryLink]
    obsoleted_by: EntryLink | None


class ActivityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    object_type: ObjectType
    object_id: uuid.UUID
    op: ChangeOp
    actor_type: ActorType
    actor_id: str | None
    reason: str | None
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    created_at: datetime


class ActivityPage(BaseModel):
    items: list[ActivityOut]
    next_cursor: uuid.UUID | None


class RetrievalLogOut(BaseModel):
    id: uuid.UUID
    query: str
    user_id: str | None
    top_score: float | None
    hit_count: int
    latency_ms: int | None
    degraded: list[str]
    gap: bool
    created_at: datetime


class RetrievalLogPage(BaseModel):
    items: list[RetrievalLogOut]
    next_cursor: uuid.UUID | None


class AddToKbIn(BaseModel):
    collection_id: uuid.UUID
    title: str = Field(min_length=1, max_length=500)
    content: str = Field(min_length=1, max_length=50_000)


class AdminJobOut(BaseModel):
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


class AdminJobPage(BaseModel):
    items: list[AdminJobOut]
    next_cursor: uuid.UUID | None


class SyncRefOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    object_type: ObjectType
    object_id: uuid.UUID
    provider: str
    external_id: str | None
    sync_status: SyncStatus
    synced_version: int | None
    last_synced_at: datetime | None
    last_error: str | None
    attempts: int


class SyncRefPage(BaseModel):
    items: list[SyncRefOut]
    next_cursor: uuid.UUID | None


class ProviderOut(BaseModel):
    role: Literal["memory", "kb"]
    name: str
    capabilities: list[str]
    healthy: bool
    detail: str | None  # why not, when not
    pending: int
    failed: int
    last_reconciled_at: datetime | None


class ProviderPage(BaseModel):
    items: list[ProviderOut]


class TemplateOut(BaseModel):
    name: str
    body: str


class SettingsOut(BaseModel):
    yaml: str
    templates: list[TemplateOut]
    embedding_model: str | None


class SettingsIn(BaseModel):
    yaml: str = Field(max_length=200_000)


class ApiKeyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    prefix: str
    scopes: list[str]
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


class ApiKeyPage(BaseModel):
    items: list[ApiKeyOut]


KeyScope = Literal["read", "write", "review"]


def _default_scopes() -> list[KeyScope]:
    return ["read", "write"]


class ApiKeyIn(BaseModel):
    name: str = Field(default="", max_length=200)
    scopes: list[KeyScope] = Field(default_factory=_default_scopes, min_length=1)


class ApiKeyCreatedOut(ApiKeyOut):
    key: str = Field(description="Shown once: store it now, it cannot be shown again.")
