import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from llm_steward.models.enums import (
    AuthorType,
    CandidateOp,
    CandidateOrigin,
    CandidateStage,
    CandidateStatus,
    CandidateTarget,
    DocStatus,
    KbOrigin,
    KbStatus,
    RiskLevel,
)
from llm_steward.schemas.bounds import Fact, UserId

POLICY_HELP = (
    'How low and medium risk are routed, e.g. {"low": "review"}: "auto" (live at once), "user" '
    '(ask the end user), "review" (a reviewer). High risk always goes to review.'
)


class CollectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    default_risk: RiskLevel = RiskLevel.MEDIUM
    review_policy: dict[str, str] = Field(default_factory=dict, description=POLICY_HELP)


class CollectionPatch(BaseModel):
    default_risk: RiskLevel | None = None
    review_policy: dict[str, str] | None = Field(default=None, description=POLICY_HELP)


class CollectionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    default_risk: RiskLevel
    review_policy: dict[str, str]
    created_at: datetime


class CollectionPage(BaseModel):
    items: list[CollectionOut]


class EntryCreate(BaseModel):
    collection_id: uuid.UUID
    title: str = Field(min_length=1, max_length=500)
    content: str = Field(min_length=1, max_length=50_000)
    risk_level: RiskLevel = Field(
        default=RiskLevel.LOW,
        description="Your own estimate; the rules (keywords, personal data) can only raise it.",
    )
    user_id: str | None = Field(
        default=None,
        max_length=200,
        description="The end user to ask about medium-risk knowledge (a confirmation card).",
    )
    tags: list[Annotated[str, Field(min_length=1, max_length=100)]] = Field(
        default_factory=list, max_length=50
    )


class EntryPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    content: str | None = Field(default=None, min_length=1, max_length=50_000)


class EntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    collection_id: uuid.UUID
    title: str
    content: str
    status: KbStatus
    risk_level: RiskLevel
    origin: KbOrigin
    author_type: AuthorType
    version: int
    created_by: str | None
    verified_by: str | None
    verified_at: datetime | None
    valid_from: datetime
    valid_to: datetime | None
    tags: list[str]
    source_refs: list[dict[str, Any]]
    created_at: datetime
    updated_at: datetime


class EntryPage(BaseModel):
    items: list[EntryOut]
    next_cursor: uuid.UUID | None


class CandidateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    target_type: CandidateTarget
    op: CandidateOp
    target_id: uuid.UUID | None
    draft: dict[str, Any]
    reason: str | None
    risk_level: RiskLevel
    origin: CandidateOrigin
    stage: CandidateStage
    status: CandidateStatus
    requested_for_user: str | None
    decided_by: str | None
    decided_at: datetime | None
    decision_note: str | None
    expires_at: datetime | None
    created_at: datetime


class CandidatePage(BaseModel):
    items: list[CandidateOut]
    next_cursor: uuid.UUID | None


class Written(BaseModel):
    """A write is either live knowledge (`entry`) or a candidate waiting for someone."""

    entry: EntryOut | None
    candidate: CandidateOut | None


class Decision(BaseModel):
    user_id: str | None = Field(
        default=None,
        max_length=200,
        description="The end user answering their card. Without it, the key's own role decides "
        "(the review queue needs a key with the review scope).",
    )
    scope: Literal["personal", "team"] = "team"
    draft: EntryPatch | None = None
    note: str | None = Field(default=None, max_length=2000)


class PromoteIn(BaseModel):
    collection_id: uuid.UUID
    user_id: str | None = Field(
        default=None, max_length=200, description="Who to ask (default: the memory's owner)."
    )


class SuggestionIn(BaseModel):
    collection_id: uuid.UUID
    text: Fact
    user_id: UserId | None = None
    session_id: str | None = Field(default=None, max_length=200)


class DecisionOut(CandidateOut):
    entry: EntryOut | None = None  # the knowledge an accepted candidate became


class DocumentOut(BaseModel):
    """`state` is the governed lifecycle (as for entries); `status` is the KB provider's parse
    status. Only an active/verified document with status `ready` is retrieved."""

    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    collection_id: uuid.UUID
    title: str
    filename: str
    mime: str | None
    size_bytes: int
    content_hash: str
    state: KbStatus
    status: DocStatus
    error: str | None
    risk_level: RiskLevel
    created_by: str | None
    valid_to: datetime | None
    created_at: datetime
    updated_at: datetime


class DocumentPage(BaseModel):
    items: list[DocumentOut]
    next_cursor: uuid.UUID | None


class DocumentWritten(BaseModel):
    """An upload: the document, and the candidate when it waits for a reviewer."""

    document: DocumentOut
    candidate: CandidateOut | None
