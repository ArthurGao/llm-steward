import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from llm_steward.schemas.kb import CandidateOut
from llm_steward.services.retrieval_service import Degraded


class RetrieveScope(BaseModel):
    user_id: str | None = None
    agent_id: str | None = None
    session_id: str | None = None


class RetrieveFilters(BaseModel):
    tags: list[str] = Field(default_factory=list, max_length=20, description="All of them (AND).")
    metadata: dict[str, str | int | float | bool] = Field(
        default_factory=dict, description="Exact values; KB documents carry none."
    )


class RetrieveIn(BaseModel):
    query: str = Field(min_length=1, max_length=4_000)
    scope: RetrieveScope = Field(default_factory=RetrieveScope)
    sources: list[Literal["memory", "kb"]] = Field(default=["memory", "kb"], min_length=1)
    collections: list[str] | None = None
    top_k: int | None = Field(
        default=None, ge=1, le=50, description="Default: the namespace's (8)."
    )
    format: Literal["items", "context"] = "items"
    as_of: datetime | None = Field(
        default=None, description="What was valid at this moment (superseded memories included)."
    )
    memory_types: list[str] | None = Field(default=None, description="Memory types to include.")
    filters: RetrieveFilters = Field(default_factory=RetrieveFilters)
    include_pinned: bool = Field(
        default=True, description="Put the user's pinned memories and profile values first."
    )
    rerank: bool = Field(
        default=False,
        description="Re-score the best fused candidates with a cross-encoder (if configured).",
    )
    token_budget: int | None = Field(
        default=None,
        ge=1,
        le=100_000,
        description="Default: the namespace's (2000). Estimated tokens of the returned items.",
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
    kind: Literal["memory", "entry", "document"] = Field(
        description="A KB hit is an entry, or a document (its matching chunks, not the file)."
    )
    passages: list[str] = Field(
        default_factory=list,
        description="A document's matching chunks, best first; `content` is them joined.",
    )


class RetrieveOut(BaseModel):
    suggestions: list[CandidateOut] = Field(
        default_factory=list, description="The scope's user's open confirmation cards (design §5)."
    )
    retrieval_id: uuid.UUID
    items: list[RetrievedItemOut]
    degraded: list[Degraded] = Field(
        description="What failed or ran out of time; the rest of the response stands."
    )
    context: str | None = None


class FeedbackIn(BaseModel):
    item_id: uuid.UUID = Field(description="One of the items this retrieval returned.")
    helpful: bool
    note: str | None = Field(default=None, max_length=2000, description="Personal data redacted.")
