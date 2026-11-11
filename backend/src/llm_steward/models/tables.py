"""Governance tables (design §4). Vectors, chunks and full-text indexes belong to the providers.

`metadata` is reserved by SQLAlchemy, so those columns are mapped to the attribute `meta`.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, LargeBinary, String, Text, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column

from llm_steward.models.base import Base, CreatedMixin, IdMixin, TimestampMixin, enum_column
from llm_steward.models.enums import (
    ActorType,
    AuthorType,
    CandidateOp,
    CandidateOrigin,
    CandidateStage,
    CandidateStatus,
    CandidateTarget,
    ChangeOp,
    DocStatus,
    EpisodeKind,
    JobStatus,
    JobType,
    KbOrigin,
    KbStatus,
    MemoryOrigin,
    MemoryStatus,
    ObjectType,
    RiskLevel,
    SyncStatus,
)


def _namespace_fk() -> Mapped[uuid.UUID]:
    return mapped_column(ForeignKey("namespaces.id", ondelete="CASCADE"), index=True)


class Namespace(IdMixin, CreatedMixin, Base):
    __tablename__ = "namespaces"
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    config: Mapped[dict[str, Any]] = mapped_column(default=dict)


class ApiKey(IdMixin, CreatedMixin, Base):
    __tablename__ = "api_keys"
    namespace_id: Mapped[uuid.UUID] = _namespace_fk()
    name: Mapped[str] = mapped_column(String(200), default="")
    prefix: Mapped[str] = mapped_column(String(16), unique=True)
    key_hash: Mapped[str] = mapped_column(String(64))
    scopes: Mapped[list[str]] = mapped_column()
    last_used_at: Mapped[datetime | None]
    revoked_at: Mapped[datetime | None]


class Episode(IdMixin, CreatedMixin, Base):
    __tablename__ = "episodes"
    __table_args__ = (
        # The buffer: unprocessed episodes of one scope (rows leave the index once extracted).
        Index(
            "ix_episodes_buffer",
            "namespace_id",
            func.coalesce(text("user_id"), ""),
            func.coalesce(text("agent_id"), ""),
            func.coalesce(text("session_id"), ""),
            "created_at",
            postgresql_where=text("processed_at IS NULL"),
            sqlite_where=text("processed_at IS NULL"),
        ),
    )
    namespace_id: Mapped[uuid.UUID] = _namespace_fk()
    kind: Mapped[EpisodeKind] = mapped_column(enum_column(EpisodeKind, "episode_kind"))
    user_id: Mapped[str | None] = mapped_column(String(200))
    agent_id: Mapped[str | None] = mapped_column(String(200))
    session_id: Mapped[str | None] = mapped_column(String(200))
    content: Mapped[dict[str, Any]] = mapped_column()
    processed_at: Mapped[datetime | None]


class Memory(IdMixin, TimestampMixin, Base):
    __tablename__ = "memories"
    __table_args__ = (
        Index("ix_memories_scope", "namespace_id", "user_id", "agent_id", "status"),
        Index("ix_memories_superseded_by", "superseded_by"),  # history walks the chain backwards
        # One current value per profile key and scope; NULL scope parts compare equal via COALESCE.
        Index(
            "uq_memories_active_profile_key",
            "namespace_id",
            func.coalesce(text("user_id"), ""),
            func.coalesce(text("agent_id"), ""),
            "key",
            unique=True,
            postgresql_where=text("status = 'active' AND key IS NOT NULL"),
            sqlite_where=text("status = 'active' AND key IS NOT NULL"),
        ),
    )
    namespace_id: Mapped[uuid.UUID] = _namespace_fk()
    user_id: Mapped[str | None] = mapped_column(String(200))
    agent_id: Mapped[str | None] = mapped_column(String(200))
    session_id: Mapped[str | None] = mapped_column(String(200))
    memory_type: Mapped[str] = mapped_column(String(64))
    key: Mapped[str | None] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text)
    data: Mapped[dict[str, Any] | None]
    status: Mapped[MemoryStatus] = mapped_column(enum_column(MemoryStatus, "memory_status"))
    origin: Mapped[MemoryOrigin] = mapped_column(enum_column(MemoryOrigin, "memory_origin"))
    pinned: Mapped[bool] = mapped_column(default=False)
    importance: Mapped[float] = mapped_column(default=0.5)
    confidence: Mapped[float] = mapped_column(default=0.8)
    valid_from: Mapped[datetime]
    valid_to: Mapped[datetime | None]
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("memories.id"))
    source_episode_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("episodes.id"))
    expires_at: Mapped[datetime | None]
    access_count: Mapped[int] = mapped_column(default=0)
    last_accessed_at: Mapped[datetime | None]
    content_hash: Mapped[str] = mapped_column(String(64))
    tags: Mapped[list[str]] = mapped_column(default=list)
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", default=dict)


class KbCollection(IdMixin, CreatedMixin, Base):
    __tablename__ = "kb_collections"
    __table_args__ = (UniqueConstraint("namespace_id", "name"),)
    namespace_id: Mapped[uuid.UUID] = _namespace_fk()
    name: Mapped[str] = mapped_column(String(200))
    review_policy: Mapped[dict[str, Any]] = mapped_column(default=dict)
    default_risk: Mapped[RiskLevel] = mapped_column(
        enum_column(RiskLevel, "collection_risk"), default=RiskLevel.MEDIUM
    )


class KbEntry(IdMixin, TimestampMixin, Base):
    __tablename__ = "kb_entries"
    # GET /v1/kb/entries: newest first by status (P3)
    __table_args__ = (Index("ix_kb_entries_list", "namespace_id", "status", "id"),)
    namespace_id: Mapped[uuid.UUID] = _namespace_fk()
    collection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("kb_collections.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(500))
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[KbStatus] = mapped_column(enum_column(KbStatus, "kb_status"))
    risk_level: Mapped[RiskLevel] = mapped_column(enum_column(RiskLevel, "kb_risk"))
    origin: Mapped[KbOrigin] = mapped_column(enum_column(KbOrigin, "kb_origin"))
    author_type: Mapped[AuthorType] = mapped_column(enum_column(AuthorType, "kb_author"))
    source_refs: Mapped[list[dict[str, Any]]] = mapped_column(default=list)
    created_by: Mapped[str | None] = mapped_column(String(200))
    verified_by: Mapped[str | None] = mapped_column(String(200))
    verified_at: Mapped[datetime | None]
    review_due_at: Mapped[datetime | None]
    valid_from: Mapped[datetime]
    valid_to: Mapped[datetime | None]
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("kb_entries.id"))
    confidence: Mapped[float | None]
    hit_count: Mapped[int] = mapped_column(default=0)
    helpful_count: Mapped[int] = mapped_column(default=0)
    unhelpful_count: Mapped[int] = mapped_column(default=0)
    version: Mapped[int] = mapped_column(default=1)
    tags: Mapped[list[str]] = mapped_column(default=list)
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", default=dict)
    content_hash: Mapped[str] = mapped_column(String(64))


class KbDocument(IdMixin, TimestampMixin, Base):
    """An uploaded document: governed as a whole (`state`), parsed and chunked by the KB provider
    (`status` is its parse status, copied from the provider)."""

    __tablename__ = "kb_documents"
    __table_args__ = (
        Index("ix_kb_documents_list", "namespace_id", "state", "id"),  # GET /v1/kb/documents
        Index("ix_kb_documents_hash", "collection_id", "content_hash"),  # one live copy per file
    )
    namespace_id: Mapped[uuid.UUID] = _namespace_fk()
    collection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("kb_collections.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(500))
    source_uri: Mapped[str | None] = mapped_column(Text)
    mime: Mapped[str | None] = mapped_column(String(200))
    content_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[DocStatus] = mapped_column(enum_column(DocStatus, "doc_status"))
    risk_level: Mapped[RiskLevel] = mapped_column(enum_column(RiskLevel, "doc_risk"))
    # P3d: the governed lifecycle (`state`, changed only by GovernanceService), the upload's own
    # facts, and why parsing failed (a fixed text). `status`/`error` are the Syncer's record of the
    # provider's parse, kept here (retrieval and the API read them) beside its external_refs row.
    state: Mapped[KbStatus] = mapped_column(enum_column(KbStatus, "doc_state"))
    filename: Mapped[str] = mapped_column(String(500))
    size_bytes: Mapped[int]
    created_by: Mapped[str | None] = mapped_column(String(200))
    valid_to: Mapped[datetime | None]
    error: Mapped[str | None] = mapped_column(String(200))

    @property
    def fetch_pending(self) -> bool:
        """Imported by URL and not fetched yet: no bytes to hand to an engine; the fetch queues
        the ingest itself (`AttachDocumentFile`)."""
        return self.source_uri is not None and self.content_hash == ""


class KbDocumentFile(Base):
    """An upload's bytes, kept only until the KB provider has them (or the document is dropped):
    written with the document row and its outbox job in one transaction."""

    __tablename__ = "kb_document_files"
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("kb_documents.id", ondelete="CASCADE"), primary_key=True
    )
    namespace_id: Mapped[uuid.UUID] = _namespace_fk()
    data: Mapped[bytes] = mapped_column(LargeBinary)


class Candidate(IdMixin, CreatedMixin, Base):
    __tablename__ = "candidates"
    __table_args__ = (
        # the review queue and an end user's cards (P3), without reading decided history
        Index("ix_candidates_queue", "namespace_id", "status", "stage", "id"),
        Index("ix_candidates_user", "namespace_id", "requested_for_user", "status", "id"),
        Index("ix_candidates_expiry", "status", "expires_at"),  # the daily expiry sweep (P3b)
    )
    namespace_id: Mapped[uuid.UUID] = _namespace_fk()
    target_type: Mapped[CandidateTarget] = mapped_column(
        enum_column(CandidateTarget, "candidate_target")
    )
    op: Mapped[CandidateOp] = mapped_column(enum_column(CandidateOp, "candidate_op"))
    target_id: Mapped[uuid.UUID | None]
    draft: Mapped[dict[str, Any]] = mapped_column()
    reason: Mapped[str | None] = mapped_column(Text)
    risk_level: Mapped[RiskLevel] = mapped_column(enum_column(RiskLevel, "candidate_risk"))
    origin: Mapped[CandidateOrigin] = mapped_column(
        enum_column(CandidateOrigin, "candidate_origin")
    )
    stage: Mapped[CandidateStage] = mapped_column(enum_column(CandidateStage, "candidate_stage"))
    status: Mapped[CandidateStatus] = mapped_column(
        enum_column(CandidateStatus, "candidate_status")
    )
    requested_for_user: Mapped[str | None] = mapped_column(String(200))
    session_id: Mapped[str | None] = mapped_column(String(200))  # cards are capped per session
    decided_by: Mapped[str | None] = mapped_column(String(200))
    decided_at: Mapped[datetime | None]
    decision_note: Mapped[str | None] = mapped_column(Text)
    source_episode_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("episodes.id"))
    expires_at: Mapped[datetime | None]


class ExternalRef(IdMixin, Base):
    __tablename__ = "external_refs"
    __table_args__ = (
        UniqueConstraint("object_type", "object_id", "provider"),
        UniqueConstraint("provider", "namespace_id", "external_id"),
    )
    namespace_id: Mapped[uuid.UUID] = _namespace_fk()
    object_type: Mapped[ObjectType] = mapped_column(enum_column(ObjectType, "ref_object_type"))
    object_id: Mapped[uuid.UUID]
    provider: Mapped[str] = mapped_column(String(64))
    external_id: Mapped[str | None] = mapped_column(String(200))
    sync_status: Mapped[SyncStatus] = mapped_column(enum_column(SyncStatus, "sync_status"))
    synced_version: Mapped[int | None]
    last_synced_at: Mapped[datetime | None]
    last_error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(default=0)


class ChangeLog(IdMixin, CreatedMixin, Base):
    __tablename__ = "change_log"
    __table_args__ = (
        Index("ix_change_log_object", "object_type", "object_id"),
        Index("ix_change_log_recent", "namespace_id", "created_at"),  # the Admin's last 7 days
    )
    namespace_id: Mapped[uuid.UUID] = _namespace_fk()
    object_type: Mapped[ObjectType] = mapped_column(enum_column(ObjectType, "log_object_type"))
    object_id: Mapped[uuid.UUID]
    op: Mapped[ChangeOp] = mapped_column(enum_column(ChangeOp, "change_op"))
    before: Mapped[dict[str, Any] | None]
    after: Mapped[dict[str, Any] | None]
    actor_type: Mapped[ActorType] = mapped_column(enum_column(ActorType, "actor_type"))
    actor_id: Mapped[str | None] = mapped_column(String(200))
    reason: Mapped[str | None] = mapped_column(Text)


class RetrievalLog(IdMixin, CreatedMixin, Base):
    __tablename__ = "retrieval_logs"
    # the hit-count pass reads the logs it has not counted yet, oldest first (P4e)
    __table_args__ = (
        Index("ix_retrieval_logs_uncounted", "counted", "id"),
        Index("ix_retrieval_logs_gaps", "namespace_id", "hit_count", "id"),  # the Admin's gaps
        Index("ix_retrieval_logs_recent", "namespace_id", "created_at"),  # its last 7 days
    )
    namespace_id: Mapped[uuid.UUID] = _namespace_fk()
    query: Mapped[str] = mapped_column(Text)
    filters: Mapped[dict[str, Any]] = mapped_column(default=dict)
    results: Mapped[list[dict[str, Any]]] = mapped_column(default=list)  # what was returned
    latency_ms: Mapped[dict[str, Any]] = mapped_column(default=dict)
    left_out: Mapped[list[dict[str, Any]]] = mapped_column(default=list)  # candidates, and why
    counted: Mapped[bool] = mapped_column(default=False)  # its hits are in the counters
    hit_count: Mapped[int] = mapped_column(default=0)  # len(results): 0 is a KB gap (P6a)


class RetrievalFeedback(IdMixin, CreatedMixin, Base):
    """Was a returned item helpful (design §7)? One row per answer, so windowed questions ("what
    was unhelpful this week") are a query, not a scan of log JSON."""

    __tablename__ = "retrieval_feedback"
    __table_args__ = (Index("ix_retrieval_feedback_recent", "namespace_id", "created_at"),)
    namespace_id: Mapped[uuid.UUID] = _namespace_fk()
    retrieval_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("retrieval_logs.id", ondelete="CASCADE"), index=True
    )
    item_id: Mapped[uuid.UUID]
    kind: Mapped[str] = mapped_column(String(16))  # memory | entry | document
    helpful: Mapped[bool]
    note: Mapped[str | None] = mapped_column(Text)  # redacted
    given_by: Mapped[str | None] = mapped_column(String(200))


class Job(IdMixin, TimestampMixin, Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_claim", "status", "run_after"),)
    namespace_id: Mapped[uuid.UUID] = _namespace_fk()
    type: Mapped[JobType] = mapped_column(enum_column(JobType, "job_type"))
    payload: Mapped[dict[str, Any]] = mapped_column()
    status: Mapped[JobStatus] = mapped_column(enum_column(JobStatus, "job_status"))
    attempts: Mapped[int] = mapped_column(default=0)
    max_attempts: Mapped[int] = mapped_column(default=8)
    run_after: Mapped[datetime]
    locked_until: Mapped[datetime | None]
    error: Mapped[str | None] = mapped_column(Text)


class IdempotencyKey(IdMixin, CreatedMixin, Base):
    """A write sent with an `Idempotency-Key` header (design §7): a retry with the same key and
    the same request replays the stored response instead of writing again. Kept 24 hours.
    `status_code` is NULL while the first request is still running."""

    __tablename__ = "idempotency_keys"
    __table_args__ = (
        UniqueConstraint("namespace_id", "principal", "key"),
        Index("ix_idempotency_keys_created", "created_at"),  # the daily purge
    )
    namespace_id: Mapped[uuid.UUID] = _namespace_fk()
    principal: Mapped[str] = mapped_column(String(300))  # the API key, and the token's user
    key: Mapped[str] = mapped_column(String(200))
    request_hash: Mapped[str] = mapped_column(String(64))
    status_code: Mapped[int | None]
    content_type: Mapped[str | None] = mapped_column(String(200))
    body: Mapped[bytes | None] = mapped_column(LargeBinary)
