"""Generated from openapi.json by scripts/generate.py. Do not edit."""

from __future__ import annotations

from typing import Annotated, Any, Literal
from pydantic import AwareDatetime, BaseModel, Field, RootModel
from uuid import UUID


class AddToKbIn(BaseModel):
    collection_id: Annotated[UUID, Field(title="Collection Id")]
    content: Annotated[str, Field(max_length=50000, min_length=1, title="Content")]
    title: Annotated[str, Field(max_length=500, min_length=1, title="Title")]


class ApiKeyCreatedOut(BaseModel):
    created_at: Annotated[AwareDatetime, Field(title="Created At")]
    id: Annotated[UUID, Field(title="Id")]
    key: Annotated[
        str,
        Field(
            description="Shown once: store it now, it cannot be shown again.",
            title="Key",
        ),
    ]
    last_used_at: Annotated[AwareDatetime | None, Field(title="Last Used At")]
    name: Annotated[str, Field(title="Name")]
    prefix: Annotated[str, Field(title="Prefix")]
    revoked_at: Annotated[AwareDatetime | None, Field(title="Revoked At")]
    scopes: Annotated[list[str], Field(title="Scopes")]


class ApiKeyIn(BaseModel):
    name: Annotated[str | None, Field(max_length=200, title="Name")] = ""
    scopes: Annotated[
        list[Literal["read", "write", "review"]] | None,
        Field(min_length=1, title="Scopes"),
    ] = None


class ApiKeyOut(BaseModel):
    created_at: Annotated[AwareDatetime, Field(title="Created At")]
    id: Annotated[UUID, Field(title="Id")]
    last_used_at: Annotated[AwareDatetime | None, Field(title="Last Used At")]
    name: Annotated[str, Field(title="Name")]
    prefix: Annotated[str, Field(title="Prefix")]
    revoked_at: Annotated[AwareDatetime | None, Field(title="Revoked At")]
    scopes: Annotated[list[str], Field(title="Scopes")]


class ApiKeyPage(BaseModel):
    items: Annotated[list[ApiKeyOut], Field(title="Items")]


class Title(RootModel[str]):
    root: Annotated[str, Field(max_length=500, title="Title")]


class Url(RootModel[str]):
    root: Annotated[str, Field(max_length=2000, title="Url")]


class AgentId(RootModel[str]):
    root: Annotated[str, Field(max_length=200, title="Agent Id")]


class SessionId(RootModel[str]):
    root: Annotated[str, Field(max_length=200, title="Session Id")]


class UserId(RootModel[str]):
    root: Annotated[str, Field(max_length=200, title="User Id")]


class BufferScope(BaseModel):
    agent_id: Annotated[AgentId | None, Field(title="Agent Id")] = None
    session_id: Annotated[SessionId | None, Field(title="Session Id")] = None
    user_id: Annotated[UserId | None, Field(title="User Id")] = None


class ChangeOut(BaseModel):
    actor_id: Annotated[str | None, Field(title="Actor Id")]
    actor_type: Annotated[str, Field(title="Actor Type")]
    after: Annotated[dict[str, Any] | None, Field(title="After")]
    before: Annotated[dict[str, Any] | None, Field(title="Before")]
    created_at: Annotated[AwareDatetime, Field(title="Created At")]
    id: Annotated[UUID, Field(title="Id")]
    op: Annotated[str, Field(title="Op")]
    reason: Annotated[str | None, Field(title="Reason")]


class ChatMessage(BaseModel):
    content: Annotated[str, Field(max_length=20000, min_length=1, title="Content")]
    role: Annotated[Literal["user", "assistant", "system"], Field(title="Role")]


class DayCountOut(BaseModel):
    count: Annotated[int, Field(title="Count")]
    day: Annotated[str, Field(title="Day")]


class Note(RootModel[str]):
    root: Annotated[str, Field(max_length=2000, title="Note")]


class UserId1(RootModel[str]):
    root: Annotated[
        str,
        Field(
            description="The end user answering their card. Without it, the key's own role decides (the review queue needs a key with the review scope).",
            max_length=200,
            title="User Id",
        ),
    ]


class Tag(RootModel[str]):
    root: Annotated[str, Field(max_length=100, min_length=1)]


class UserId2(RootModel[str]):
    root: Annotated[
        str,
        Field(
            description="The end user to ask about medium-risk knowledge (a confirmation card).",
            max_length=200,
            title="User Id",
        ),
    ]


class EntryLink(BaseModel):
    id: Annotated[UUID, Field(title="Id")]
    status: Annotated[str, Field(title="Status")]
    title: Annotated[str, Field(title="Title")]
    version: Annotated[int, Field(title="Version")]


class Content(RootModel[str]):
    root: Annotated[str, Field(max_length=50000, min_length=1, title="Content")]


class Title1(RootModel[str]):
    root: Annotated[str, Field(max_length=500, min_length=1, title="Title")]


class EntryPatch(BaseModel):
    content: Annotated[Content | None, Field(title="Content")] = None
    title: Annotated[Title1 | None, Field(title="Title")] = None


class EntryVersionOut(BaseModel):
    """
    One version of an entry: what its text was from `changed_at` on.
    """

    actor_id: Annotated[str | None, Field(title="Actor Id")]
    actor_type: Annotated[
        Literal["ai", "end_user", "admin", "system", "provider", "host_app"],
        Field(title="ActorType"),
    ]
    changed_at: Annotated[AwareDatetime, Field(title="Changed At")]
    content: Annotated[str, Field(title="Content")]
    reason: Annotated[str | None, Field(title="Reason")]
    status: Annotated[str, Field(title="Status")]
    title: Annotated[str, Field(title="Title")]
    version: Annotated[int, Field(title="Version")]


class UserId3(RootModel[str]):
    root: Annotated[str, Field(max_length=200, title="User Id")]


class EpisodeCreate(BaseModel):
    agent_id: Annotated[AgentId | None, Field(title="Agent Id")] = None
    messages: Annotated[
        list[ChatMessage], Field(max_length=100, min_length=1, title="Messages")
    ]
    mode: Annotated[
        Literal["async", "sync"] | None,
        Field(
            description="async: buffer and extract later. sync: extract now and return the memories.",
            title="Mode",
        ),
    ] = "async"
    session_id: Annotated[SessionId | None, Field(title="Session Id")] = None
    user_id: Annotated[UserId3 | None, Field(title="User Id")] = None


class ErasedOut(BaseModel):
    """
    What one user's erasure removed (counts only).
    """

    candidates: Annotated[int, Field(title="Candidates")]
    episodes: Annotated[int, Field(title="Episodes")]
    memories: Annotated[int, Field(title="Memories")]
    retrieval_logs: Annotated[int, Field(title="Retrieval Logs")]
    user_id: Annotated[str, Field(title="User Id")]


class Note1(RootModel[str]):
    root: Annotated[
        str, Field(description="Personal data redacted.", max_length=2000, title="Note")
    ]


class FeedbackIn(BaseModel):
    helpful: Annotated[bool, Field(title="Helpful")]
    item_id: Annotated[
        UUID,
        Field(description="One of the items this retrieval returned.", title="Item Id"),
    ]
    note: Annotated[
        Note1 | None, Field(description="Personal data redacted.", title="Note")
    ] = None


class FlushResult(BaseModel):
    job_id: Annotated[UUID | None, Field(title="Job Id")]


class GapOut(BaseModel):
    count: Annotated[int, Field(title="Count")]
    query: Annotated[str, Field(title="Query")]


class Key(RootModel[str]):
    root: Annotated[
        str,
        Field(
            description="Profile key (e.g. user.city): one current value per user (and agent); a new value supersedes the old one. Not allowed together with session_id.",
            max_length=200,
            title="Key",
        ),
    ]


class MemoryCreate(BaseModel):
    agent_id: Annotated[AgentId | None, Field(title="Agent Id")] = None
    confidence: Annotated[float | None, Field(ge=0.0, le=1.0, title="Confidence")] = 0.8
    content: Annotated[str, Field(max_length=20000, min_length=1, title="Content")]
    importance: Annotated[float | None, Field(ge=0.0, le=1.0, title="Importance")] = 0.5
    infer: Annotated[
        bool | None,
        Field(
            description="Let the memory provider extract facts. Needs the 'extract' capability (P2).",
            title="Infer",
        ),
    ] = False
    key: Annotated[
        Key | None,
        Field(
            description="Profile key (e.g. user.city): one current value per user (and agent); a new value supersedes the old one. Not allowed together with session_id.",
            title="Key",
        ),
    ] = None
    memory_type: Annotated[str | None, Field(max_length=64, title="Memory Type")] = (
        "semantic"
    )
    metadata: Annotated[dict[str, Any] | None, Field(title="Metadata")] = None
    pinned: Annotated[bool | None, Field(title="Pinned")] = False
    session_id: Annotated[SessionId | None, Field(title="Session Id")] = None
    tags: Annotated[list[str] | None, Field(max_length=50, title="Tags")] = None
    user_id: Annotated[UserId3 | None, Field(title="User Id")] = None


class Confidence(RootModel[float]):
    root: Annotated[float, Field(ge=0.0, le=1.0, title="Confidence")]


class Importance(RootModel[float]):
    root: Annotated[float, Field(ge=0.0, le=1.0, title="Importance")]


class Tags(RootModel[list[str]]):
    root: Annotated[list[str], Field(max_length=50, title="Tags")]


class MemoryPatch(BaseModel):
    confidence: Annotated[Confidence | None, Field(title="Confidence")] = None
    importance: Annotated[Importance | None, Field(title="Importance")] = None
    metadata: Annotated[dict[str, Any] | None, Field(title="Metadata")] = None
    pinned: Annotated[bool | None, Field(title="Pinned")] = None
    tags: Annotated[Tags | None, Field(title="Tags")] = None


class NamespaceOut(BaseModel):
    id: Annotated[UUID, Field(title="Id")]
    kb_provider: Annotated[str, Field(title="Kb Provider")]
    memory_provider: Annotated[str, Field(title="Memory Provider")]
    name: Annotated[str, Field(title="Name")]
    slug: Annotated[str, Field(title="Slug")]


class NamespacePage(BaseModel):
    items: Annotated[list[NamespaceOut], Field(title="Items")]


class UserId5(RootModel[str]):
    root: Annotated[
        str,
        Field(
            description="Who to ask (default: the memory's owner).",
            max_length=200,
            title="User Id",
        ),
    ]


class PromoteIn(BaseModel):
    collection_id: Annotated[UUID, Field(title="Collection Id")]
    user_id: Annotated[
        UserId5 | None,
        Field(description="Who to ask (default: the memory's owner).", title="User Id"),
    ] = None


class ProviderOut(BaseModel):
    capabilities: Annotated[list[str], Field(title="Capabilities")]
    detail: Annotated[str | None, Field(title="Detail")]
    failed: Annotated[int, Field(title="Failed")]
    healthy: Annotated[bool, Field(title="Healthy")]
    last_reconciled_at: Annotated[
        AwareDatetime | None, Field(title="Last Reconciled At")
    ]
    name: Annotated[str, Field(title="Name")]
    pending: Annotated[int, Field(title="Pending")]
    role: Annotated[Literal["memory", "kb"], Field(title="Role")]


class ProviderPage(BaseModel):
    items: Annotated[list[ProviderOut], Field(title="Items")]


class RetrievalLogOut(BaseModel):
    created_at: Annotated[AwareDatetime, Field(title="Created At")]
    degraded: Annotated[list[str], Field(title="Degraded")]
    gap: Annotated[bool, Field(title="Gap")]
    hit_count: Annotated[int, Field(title="Hit Count")]
    id: Annotated[UUID, Field(title="Id")]
    latency_ms: Annotated[int | None, Field(title="Latency Ms")]
    query: Annotated[str, Field(title="Query")]
    top_score: Annotated[float | None, Field(title="Top Score")]
    user_id: Annotated[str | None, Field(title="User Id")]


class RetrievalLogPage(BaseModel):
    items: Annotated[list[RetrievalLogOut], Field(title="Items")]
    next_cursor: Annotated[UUID | None, Field(title="Next Cursor")]


class RetrieveFilters(BaseModel):
    metadata: Annotated[
        dict[str, str | int | float | bool] | None,
        Field(description="Exact values; KB documents carry none.", title="Metadata"),
    ] = None
    tags: Annotated[
        list[str] | None,
        Field(description="All of them (AND).", max_length=20, title="Tags"),
    ] = None


class TokenBudget(RootModel[int]):
    root: Annotated[
        int,
        Field(
            description="Default: the namespace's (2000). Estimated tokens of the returned items.",
            ge=1,
            le=100000,
            title="Token Budget",
        ),
    ]


class TopK(RootModel[int]):
    root: Annotated[
        int,
        Field(description="Default: the namespace's (8).", ge=1, le=50, title="Top K"),
    ]


class RetrieveScope(BaseModel):
    agent_id: Annotated[str | None, Field(title="Agent Id")] = None
    session_id: Annotated[str | None, Field(title="Session Id")] = None
    user_id: Annotated[str | None, Field(title="User Id")] = None


class RetrievedItemOut(BaseModel):
    content: Annotated[str, Field(title="Content")]
    id: Annotated[UUID, Field(title="Id")]
    kind: Annotated[
        Literal["memory", "entry", "document"],
        Field(
            description="A KB hit is an entry, or a document (its matching chunks, not the file).",
            title="Kind",
        ),
    ]
    passages: Annotated[
        list[str] | None,
        Field(
            description="A document's matching chunks, best first; `content` is them joined.",
            title="Passages",
        ),
    ] = None
    pinned: Annotated[bool, Field(title="Pinned")]
    score: Annotated[float, Field(title="Score")]
    source: Annotated[Literal["memory", "kb"], Field(title="Source")]
    status: Annotated[str, Field(title="Status")]
    title: Annotated[str | None, Field(title="Title")]
    verified: Annotated[bool, Field(title="Verified")]


class SettingsIn(BaseModel):
    yaml: Annotated[str, Field(max_length=200000, title="Yaml")]


class SourceRef(BaseModel):
    id: Annotated[str, Field(title="Id")]
    type: Annotated[str, Field(title="Type")]


class StatsOut(BaseModel):
    awaiting_user: Annotated[int, Field(title="Awaiting User")]
    documents: Annotated[int, Field(title="Documents")]
    failed_jobs: Annotated[int, Field(title="Failed Jobs")]
    gaps: Annotated[list[GapOut], Field(title="Gaps")]
    kb_entries: Annotated[dict[str, int], Field(title="Kb Entries")]
    memories: Annotated[dict[str, int], Field(title="Memories")]
    pending_reviews: Annotated[int, Field(title="Pending Reviews")]
    retrieval_p95_ms: Annotated[int | None, Field(title="Retrieval P95 Ms")]
    retrievals_7d: Annotated[list[DayCountOut], Field(title="Retrievals 7D")]
    writes_7d: Annotated[list[DayCountOut], Field(title="Writes 7D")]


class UserId6(RootModel[str]):
    root: Annotated[str, Field(max_length=200, title="User Id")]


class SuggestionIn(BaseModel):
    collection_id: Annotated[UUID, Field(title="Collection Id")]
    session_id: Annotated[SessionId | None, Field(title="Session Id")] = None
    text: Annotated[str, Field(max_length=20000, min_length=1, title="Text")]
    user_id: Annotated[UserId6 | None, Field(title="User Id")] = None


class TemplateOut(BaseModel):
    body: Annotated[str, Field(title="Body")]
    name: Annotated[str, Field(title="Name")]


class UserSummaryOut(BaseModel):
    last_active_at: Annotated[AwareDatetime, Field(title="Last Active At")]
    memory_count: Annotated[int, Field(title="Memory Count")]
    pinned_count: Annotated[int, Field(title="Pinned Count")]
    user_id: Annotated[str, Field(title="User Id")]


class UserTokenIn(BaseModel):
    ttl_seconds: Annotated[
        int | None,
        Field(
            description="Lifetime in seconds: 15 minutes by default, 1 hour at most.",
            ge=60,
            le=3600,
            title="Ttl Seconds",
        ),
    ] = 900
    user_id: Annotated[str, Field(max_length=200, min_length=1, title="User Id")]


class UserTokenOut(BaseModel):
    expires_at: Annotated[AwareDatetime, Field(title="Expires At")]
    token: Annotated[str, Field(title="Token")]
    token_type: Annotated[Literal["Bearer"], Field(title="Token Type")] = "Bearer"
    user_id: Annotated[str, Field(title="User Id")]


class ValidationError(BaseModel):
    ctx: Annotated[dict[str, Any] | None, Field(title="Context")] = None
    input: Annotated[Any | None, Field(title="Input")] = None
    loc: Annotated[list[str | int], Field(title="Location")]
    msg: Annotated[str, Field(title="Message")]
    type: Annotated[str, Field(title="Error Type")]


class ActivityOut(BaseModel):
    actor_id: Annotated[str | None, Field(title="Actor Id")]
    actor_type: Annotated[
        Literal["ai", "end_user", "admin", "system", "provider", "host_app"],
        Field(title="ActorType"),
    ]
    after: Annotated[dict[str, Any] | None, Field(title="After")]
    before: Annotated[dict[str, Any] | None, Field(title="Before")]
    created_at: Annotated[AwareDatetime, Field(title="Created At")]
    id: Annotated[UUID, Field(title="Id")]
    object_id: Annotated[UUID, Field(title="Object Id")]
    object_type: Annotated[
        Literal[
            "memory", "kb_entry", "kb_document", "kb_collection", "candidate", "episode"
        ],
        Field(title="ObjectType"),
    ]
    op: Annotated[Literal["create", "update", "transition"], Field(title="ChangeOp")]
    reason: Annotated[str | None, Field(title="Reason")]


class ActivityPage(BaseModel):
    items: Annotated[list[ActivityOut], Field(title="Items")]
    next_cursor: Annotated[UUID | None, Field(title="Next Cursor")]


class AdminJobOut(BaseModel):
    attempts: Annotated[int, Field(title="Attempts")]
    created_at: Annotated[AwareDatetime, Field(title="Created At")]
    error: Annotated[str | None, Field(title="Error")]
    id: Annotated[UUID, Field(title="Id")]
    max_attempts: Annotated[int, Field(title="Max Attempts")]
    run_after: Annotated[AwareDatetime, Field(title="Run After")]
    status: Annotated[
        Literal["pending", "running", "done", "failed"], Field(title="JobStatus")
    ]
    type: Annotated[
        Literal[
            "sync.memory_upsert",
            "sync.memory_remove",
            "sync.kb_upsert",
            "sync.kb_remove",
            "memory.extract",
            "memory.reconcile",
            "kb.suggest",
            "kb.doc_ingest",
            "kb.doc_status",
            "kb.doc_remove",
            "kb.doc_fetch",
            "webhook.deliver",
        ],
        Field(title="JobType"),
    ]
    updated_at: Annotated[AwareDatetime, Field(title="Updated At")]


class AdminJobPage(BaseModel):
    items: Annotated[list[AdminJobOut], Field(title="Items")]
    next_cursor: Annotated[UUID | None, Field(title="Next Cursor")]


class BodyUploadDocumentV1KbDocumentsPost(BaseModel):
    collection_id: Annotated[UUID, Field(title="Collection Id")]
    file: Annotated[str | None, Field(title="File")] = None
    risk_level: Annotated[
        Literal["low", "medium", "high"] | None, Field(title="RiskLevel")
    ] = "low"
    title: Annotated[Title | None, Field(title="Title")] = None
    url: Annotated[Url | None, Field(title="Url")] = None


class CandidateOut(BaseModel):
    created_at: Annotated[AwareDatetime, Field(title="Created At")]
    decided_at: Annotated[AwareDatetime | None, Field(title="Decided At")]
    decided_by: Annotated[str | None, Field(title="Decided By")]
    decision_note: Annotated[str | None, Field(title="Decision Note")]
    draft: Annotated[dict[str, Any], Field(title="Draft")]
    expires_at: Annotated[AwareDatetime | None, Field(title="Expires At")]
    id: Annotated[UUID, Field(title="Id")]
    op: Annotated[Literal["ADD", "UPDATE", "DELETE"], Field(title="CandidateOp")]
    origin: Annotated[
        Literal["extraction", "promote", "user_suggestion", "api"],
        Field(title="CandidateOrigin"),
    ]
    reason: Annotated[str | None, Field(title="Reason")]
    requested_for_user: Annotated[str | None, Field(title="Requested For User")]
    risk_level: Annotated[Literal["low", "medium", "high"], Field(title="RiskLevel")]
    stage: Annotated[
        Literal["awaiting_user", "awaiting_review"], Field(title="CandidateStage")
    ]
    status: Annotated[
        Literal["pending", "accepted", "rejected", "expired"],
        Field(title="CandidateStatus"),
    ]
    target_id: Annotated[UUID | None, Field(title="Target Id")]
    target_type: Annotated[
        Literal["memory", "kb_entry", "kb_document"], Field(title="CandidateTarget")
    ]


class CandidatePage(BaseModel):
    items: Annotated[list[CandidateOut], Field(title="Items")]
    next_cursor: Annotated[UUID | None, Field(title="Next Cursor")]


class CollectionCountsOut(BaseModel):
    created_at: Annotated[AwareDatetime, Field(title="Created At")]
    default_risk: Annotated[Literal["low", "medium", "high"], Field(title="RiskLevel")]
    document_count: Annotated[int, Field(title="Document Count")]
    entry_count: Annotated[int, Field(title="Entry Count")]
    id: Annotated[UUID, Field(title="Id")]
    name: Annotated[str, Field(title="Name")]
    review_policy: Annotated[dict[str, str], Field(title="Review Policy")]


class CollectionCountsPage(BaseModel):
    items: Annotated[list[CollectionCountsOut], Field(title="Items")]


class CollectionCreate(BaseModel):
    default_risk: Annotated[
        Literal["low", "medium", "high"] | None, Field(title="RiskLevel")
    ] = "medium"
    name: Annotated[str, Field(max_length=200, min_length=1, title="Name")]
    review_policy: Annotated[
        dict[str, str] | None,
        Field(
            description='How low and medium risk are routed, e.g. {"low": "review"}: "auto" (live at once), "user" (ask the end user), "review" (a reviewer). High risk always goes to review.',
            title="Review Policy",
        ),
    ] = None


class CollectionOut(BaseModel):
    created_at: Annotated[AwareDatetime, Field(title="Created At")]
    default_risk: Annotated[Literal["low", "medium", "high"], Field(title="RiskLevel")]
    id: Annotated[UUID, Field(title="Id")]
    name: Annotated[str, Field(title="Name")]
    review_policy: Annotated[dict[str, str], Field(title="Review Policy")]


class CollectionPage(BaseModel):
    items: Annotated[list[CollectionOut], Field(title="Items")]


class CollectionPatch(BaseModel):
    default_risk: Literal["low", "medium", "high"] | None = None
    review_policy: Annotated[
        dict[str, str] | None,
        Field(
            description='How low and medium risk are routed, e.g. {"low": "review"}: "auto" (live at once), "user" (ask the end user), "review" (a reviewer). High risk always goes to review.',
            title="Review Policy",
        ),
    ] = None


class Decision(BaseModel):
    draft: EntryPatch | None = None
    note: Annotated[Note | None, Field(title="Note")] = None
    scope: Annotated[Literal["personal", "team"] | None, Field(title="Scope")] = "team"
    user_id: Annotated[
        UserId1 | None,
        Field(
            description="The end user answering their card. Without it, the key's own role decides (the review queue needs a key with the review scope).",
            title="User Id",
        ),
    ] = None


class DocumentOut(BaseModel):
    """
    `state` is the governed lifecycle (as for entries); `status` is the KB provider's parse
    status. Only an active/verified document with status `ready` is retrieved.
    """

    collection_id: Annotated[UUID, Field(title="Collection Id")]
    content_hash: Annotated[str, Field(title="Content Hash")]
    created_at: Annotated[AwareDatetime, Field(title="Created At")]
    created_by: Annotated[str | None, Field(title="Created By")]
    error: Annotated[str | None, Field(title="Error")]
    filename: Annotated[str, Field(title="Filename")]
    id: Annotated[UUID, Field(title="Id")]
    mime: Annotated[str | None, Field(title="Mime")]
    risk_level: Annotated[Literal["low", "medium", "high"], Field(title="RiskLevel")]
    size_bytes: Annotated[int, Field(title="Size Bytes")]
    state: Annotated[
        Literal["draft", "active", "verified", "deprecated", "rejected"],
        Field(title="KbStatus"),
    ]
    status: Annotated[
        Literal["pending", "processing", "ready", "failed"], Field(title="DocStatus")
    ]
    title: Annotated[str, Field(title="Title")]
    updated_at: Annotated[AwareDatetime, Field(title="Updated At")]
    valid_to: Annotated[AwareDatetime | None, Field(title="Valid To")]


class DocumentPage(BaseModel):
    items: Annotated[list[DocumentOut], Field(title="Items")]
    next_cursor: Annotated[UUID | None, Field(title="Next Cursor")]


class DocumentWritten(BaseModel):
    """
    An upload: the document, and the candidate when it waits for a reviewer.
    """

    candidate: CandidateOut | None
    document: DocumentOut


class EntryCreate(BaseModel):
    collection_id: Annotated[UUID, Field(title="Collection Id")]
    content: Annotated[str, Field(max_length=50000, min_length=1, title="Content")]
    risk_level: Annotated[
        Literal["low", "medium", "high"] | None,
        Field(
            description="Your own estimate; the rules (keywords, personal data) can only raise it.",
            title="RiskLevel",
        ),
    ] = "low"
    tags: Annotated[list[Tag] | None, Field(max_length=50, title="Tags")] = None
    title: Annotated[str, Field(max_length=500, min_length=1, title="Title")]
    user_id: Annotated[
        UserId2 | None,
        Field(
            description="The end user to ask about medium-risk knowledge (a confirmation card).",
            title="User Id",
        ),
    ] = None


class EntryHistoryOut(BaseModel):
    entry_id: Annotated[UUID, Field(title="Entry Id")]
    obsoleted_by: EntryLink | None
    obsoletes: Annotated[list[EntryLink], Field(title="Obsoletes")]
    versions: Annotated[list[EntryVersionOut], Field(title="Versions")]


class EntryOut(BaseModel):
    author_type: Annotated[Literal["ai", "human"], Field(title="AuthorType")]
    collection_id: Annotated[UUID, Field(title="Collection Id")]
    content: Annotated[str, Field(title="Content")]
    created_at: Annotated[AwareDatetime, Field(title="Created At")]
    created_by: Annotated[str | None, Field(title="Created By")]
    helpful_count: Annotated[int | None, Field(title="Helpful Count")] = 0
    hit_count: Annotated[int | None, Field(title="Hit Count")] = 0
    id: Annotated[UUID, Field(title="Id")]
    origin: Annotated[
        Literal["manual", "import", "promoted_memory", "user_suggestion", "extraction"],
        Field(title="KbOrigin"),
    ]
    risk_level: Annotated[Literal["low", "medium", "high"], Field(title="RiskLevel")]
    source_refs: Annotated[list[dict[str, Any]], Field(title="Source Refs")]
    status: Annotated[
        Literal["draft", "active", "verified", "deprecated", "rejected"],
        Field(title="KbStatus"),
    ]
    superseded_by: Annotated[UUID | None, Field(title="Superseded By")] = None
    tags: Annotated[list[str], Field(title="Tags")]
    title: Annotated[str, Field(title="Title")]
    unhelpful_count: Annotated[int | None, Field(title="Unhelpful Count")] = 0
    updated_at: Annotated[AwareDatetime, Field(title="Updated At")]
    valid_from: Annotated[AwareDatetime, Field(title="Valid From")]
    valid_to: Annotated[AwareDatetime | None, Field(title="Valid To")]
    verified_at: Annotated[AwareDatetime | None, Field(title="Verified At")]
    verified_by: Annotated[str | None, Field(title="Verified By")]
    version: Annotated[int, Field(title="Version")]


class EntryPage(BaseModel):
    items: Annotated[list[EntryOut], Field(title="Items")]
    next_cursor: Annotated[UUID | None, Field(title="Next Cursor")]


class HTTPValidationError(BaseModel):
    detail: Annotated[list[ValidationError] | None, Field(title="Detail")] = None


class JobOut(BaseModel):
    attempts: Annotated[int, Field(title="Attempts")]
    created_at: Annotated[AwareDatetime, Field(title="Created At")]
    error: Annotated[str | None, Field(title="Error")]
    id: Annotated[UUID, Field(title="Id")]
    max_attempts: Annotated[int, Field(title="Max Attempts")]
    run_after: Annotated[AwareDatetime, Field(title="Run After")]
    status: Annotated[
        Literal["pending", "running", "done", "failed"], Field(title="JobStatus")
    ]
    type: Annotated[
        Literal[
            "sync.memory_upsert",
            "sync.memory_remove",
            "sync.kb_upsert",
            "sync.kb_remove",
            "memory.extract",
            "memory.reconcile",
            "kb.suggest",
            "kb.doc_ingest",
            "kb.doc_status",
            "kb.doc_remove",
            "kb.doc_fetch",
            "webhook.deliver",
        ],
        Field(title="JobType"),
    ]
    updated_at: Annotated[AwareDatetime, Field(title="Updated At")]


class MemoryBrief(BaseModel):
    """
    One version of a fact in a supersede chain.
    """

    content: Annotated[str, Field(title="Content")]
    id: Annotated[UUID, Field(title="Id")]
    status: Annotated[
        Literal["active", "superseded", "deleted"], Field(title="MemoryStatus")
    ]
    superseded_by: Annotated[UUID | None, Field(title="Superseded By")]
    valid_from: Annotated[AwareDatetime, Field(title="Valid From")]
    valid_to: Annotated[AwareDatetime | None, Field(title="Valid To")]


class MemoryHistoryOut(BaseModel):
    chain: Annotated[list[MemoryBrief], Field(title="Chain")]
    changes: Annotated[list[ChangeOut], Field(title="Changes")]
    memory_id: Annotated[UUID, Field(title="Memory Id")]


class MemoryOut(BaseModel):
    agent_id: Annotated[str | None, Field(title="Agent Id")]
    confidence: Annotated[float, Field(title="Confidence")]
    content: Annotated[str, Field(title="Content")]
    created_at: Annotated[AwareDatetime, Field(title="Created At")]
    id: Annotated[UUID, Field(title="Id")]
    importance: Annotated[float, Field(title="Importance")]
    memory_type: Annotated[str, Field(title="Memory Type")]
    metadata: Annotated[dict[str, Any], Field(title="Metadata")]
    origin: Annotated[
        Literal["extraction", "direct", "import"], Field(title="MemoryOrigin")
    ]
    pinned: Annotated[bool, Field(title="Pinned")]
    session_id: Annotated[str | None, Field(title="Session Id")]
    source_refs: Annotated[list[SourceRef], Field(title="Source Refs")]
    status: Annotated[
        Literal["active", "superseded", "deleted"], Field(title="MemoryStatus")
    ]
    sync_status: Annotated[
        Literal["pending", "synced", "failed", "deleting", "deleted"],
        Field(title="SyncStatus"),
    ]
    tags: Annotated[list[str], Field(title="Tags")]
    updated_at: Annotated[AwareDatetime, Field(title="Updated At")]
    user_id: Annotated[str | None, Field(title="User Id")]
    valid_from: Annotated[AwareDatetime, Field(title="Valid From")]
    valid_to: Annotated[AwareDatetime | None, Field(title="Valid To")]


class MemoryPage(BaseModel):
    items: Annotated[list[MemoryOut], Field(title="Items")]
    next_cursor: Annotated[UUID | None, Field(title="Next Cursor")]


class ProfileOut(BaseModel):
    pinned: Annotated[list[MemoryBrief], Field(title="Pinned")]
    user_id: Annotated[str, Field(title="User Id")]
    values: Annotated[dict[str, str], Field(title="Values")]


class RetrieveIn(BaseModel):
    as_of: Annotated[
        AwareDatetime | None,
        Field(
            description="What was valid at this moment (superseded memories included).",
            title="As Of",
        ),
    ] = None
    collections: Annotated[list[str] | None, Field(title="Collections")] = None
    filters: RetrieveFilters | None = None
    format: Annotated[Literal["items", "context"] | None, Field(title="Format")] = (
        "items"
    )
    include_pinned: Annotated[
        bool | None,
        Field(
            description="Put the user's pinned memories and profile values first.",
            title="Include Pinned",
        ),
    ] = True
    memory_types: Annotated[
        list[str] | None,
        Field(description="Memory types to include.", title="Memory Types"),
    ] = None
    query: Annotated[str, Field(max_length=4000, min_length=1, title="Query")]
    rerank: Annotated[
        bool | None,
        Field(
            description="Re-score the best fused candidates with a cross-encoder. Default: the namespace's `retrieval.rerank` (off).",
            title="Rerank",
        ),
    ] = None
    scope: RetrieveScope | None = None
    sources: Annotated[
        list[Literal["memory", "kb"]] | None, Field(min_length=1, title="Sources")
    ] = ["memory", "kb"]
    token_budget: Annotated[
        TokenBudget | None,
        Field(
            description="Default: the namespace's (2000). Estimated tokens of the returned items.",
            title="Token Budget",
        ),
    ] = None
    top_k: Annotated[
        TopK | None, Field(description="Default: the namespace's (8).", title="Top K")
    ] = None


class RetrieveOut(BaseModel):
    context: Annotated[str | None, Field(title="Context")] = None
    degraded: Annotated[
        list[Literal["memory", "kb", "rerank"]],
        Field(
            description="What failed or ran out of time; the rest of the response stands.",
            title="Degraded",
        ),
    ]
    items: Annotated[list[RetrievedItemOut], Field(title="Items")]
    retrieval_id: Annotated[UUID, Field(title="Retrieval Id")]
    suggestions: Annotated[
        list[CandidateOut] | None,
        Field(
            description="The scope's user's open confirmation cards (design §5).",
            title="Suggestions",
        ),
    ] = None


class SettingsOut(BaseModel):
    embedding_model: Annotated[str | None, Field(title="Embedding Model")]
    templates: Annotated[list[TemplateOut], Field(title="Templates")]
    yaml: Annotated[str, Field(title="Yaml")]


class SyncRefOut(BaseModel):
    attempts: Annotated[int, Field(title="Attempts")]
    external_id: Annotated[str | None, Field(title="External Id")]
    id: Annotated[UUID, Field(title="Id")]
    last_error: Annotated[str | None, Field(title="Last Error")]
    last_synced_at: Annotated[AwareDatetime | None, Field(title="Last Synced At")]
    object_id: Annotated[UUID, Field(title="Object Id")]
    object_type: Annotated[
        Literal[
            "memory", "kb_entry", "kb_document", "kb_collection", "candidate", "episode"
        ],
        Field(title="ObjectType"),
    ]
    provider: Annotated[str, Field(title="Provider")]
    sync_status: Annotated[
        Literal["pending", "synced", "failed", "deleting", "deleted"],
        Field(title="SyncStatus"),
    ]
    synced_version: Annotated[int | None, Field(title="Synced Version")]


class SyncRefPage(BaseModel):
    items: Annotated[list[SyncRefOut], Field(title="Items")]
    next_cursor: Annotated[UUID | None, Field(title="Next Cursor")]


class UserDetailOut(BaseModel):
    last_active_at: Annotated[AwareDatetime, Field(title="Last Active At")]
    memory_count: Annotated[int, Field(title="Memory Count")]
    pinned: Annotated[list[MemoryBrief], Field(title="Pinned")]
    pinned_count: Annotated[int, Field(title="Pinned Count")]
    profile: Annotated[dict[str, str], Field(title="Profile")]
    user_id: Annotated[str, Field(title="User Id")]


class UserPage(BaseModel):
    items: Annotated[list[UserSummaryOut], Field(title="Items")]


class Written(BaseModel):
    """
    A write is either live knowledge (`entry`) or a candidate waiting for someone.
    """

    candidate: CandidateOut | None
    entry: EntryOut | None


class DecisionOut(BaseModel):
    created_at: Annotated[AwareDatetime, Field(title="Created At")]
    decided_at: Annotated[AwareDatetime | None, Field(title="Decided At")]
    decided_by: Annotated[str | None, Field(title="Decided By")]
    decision_note: Annotated[str | None, Field(title="Decision Note")]
    draft: Annotated[dict[str, Any], Field(title="Draft")]
    entry: EntryOut | None = None
    expires_at: Annotated[AwareDatetime | None, Field(title="Expires At")]
    id: Annotated[UUID, Field(title="Id")]
    op: Annotated[Literal["ADD", "UPDATE", "DELETE"], Field(title="CandidateOp")]
    origin: Annotated[
        Literal["extraction", "promote", "user_suggestion", "api"],
        Field(title="CandidateOrigin"),
    ]
    reason: Annotated[str | None, Field(title="Reason")]
    requested_for_user: Annotated[str | None, Field(title="Requested For User")]
    risk_level: Annotated[Literal["low", "medium", "high"], Field(title="RiskLevel")]
    stage: Annotated[
        Literal["awaiting_user", "awaiting_review"], Field(title="CandidateStage")
    ]
    status: Annotated[
        Literal["pending", "accepted", "rejected", "expired"],
        Field(title="CandidateStatus"),
    ]
    target_id: Annotated[UUID | None, Field(title="Target Id")]
    target_type: Annotated[
        Literal["memory", "kb_entry", "kb_document"], Field(title="CandidateTarget")
    ]


class EpisodeAccepted(BaseModel):
    episode_id: Annotated[UUID, Field(title="Episode Id")]
    job_id: Annotated[UUID | None, Field(title="Job Id")] = None
    memories: Annotated[list[MemoryOut] | None, Field(title="Memories")] = None
    status: Annotated[Literal["buffered", "queued", "processed"], Field(title="Status")]
    suggestions: Annotated[
        list[CandidateOut] | None,
        Field(
            description="The user's open confirmation cards (design §5).",
            title="Suggestions",
        ),
    ] = None
