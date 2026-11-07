"""Governance tables (design §4).

Generated with autogenerate, then frozen: no imports from llm_steward, so later model changes
cannot alter what this revision creates.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-08 11:16:23.069245
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "admin_users",
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.String(length=200), nullable=False),
        sa.Column(
            "role",
            sa.Enum(
                "owner",
                "reviewer",
                "viewer",
                name="admin_role",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "namespaces",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    op.create_table(
        "namespaces",
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "config",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )
    op.create_table(
        "api_keys",
        sa.Column("namespace_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("prefix", sa.String(length=16), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "scopes",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["namespace_id"], ["namespaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("prefix"),
    )
    with op.batch_alter_table("api_keys", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_api_keys_namespace_id"), ["namespace_id"], unique=False
        )

    op.create_table(
        "change_log",
        sa.Column("namespace_id", sa.Uuid(), nullable=False),
        sa.Column(
            "object_type",
            sa.Enum(
                "memory",
                "kb_entry",
                "kb_document",
                "kb_collection",
                "candidate",
                name="log_object_type",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("object_id", sa.Uuid(), nullable=False),
        sa.Column(
            "op",
            sa.Enum(
                "create",
                "update",
                "transition",
                name="change_op",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "before",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column(
            "after",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column(
            "actor_type",
            sa.Enum(
                "ai",
                "end_user",
                "admin",
                "system",
                "provider",
                name="actor_type",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("actor_id", sa.String(length=200), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["namespace_id"], ["namespaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("change_log", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_change_log_namespace_id"), ["namespace_id"], unique=False
        )
        batch_op.create_index("ix_change_log_object", ["object_type", "object_id"], unique=False)

    op.create_table(
        "episodes",
        sa.Column("namespace_id", sa.Uuid(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "conversation",
                "import",
                "manual",
                name="episode_kind",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("user_id", sa.String(length=200), nullable=True),
        sa.Column("agent_id", sa.String(length=200), nullable=True),
        sa.Column("session_id", sa.String(length=200), nullable=True),
        sa.Column(
            "content",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["namespace_id"], ["namespaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("episodes", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_episodes_namespace_id"), ["namespace_id"], unique=False
        )

    op.create_table(
        "external_refs",
        sa.Column("namespace_id", sa.Uuid(), nullable=False),
        sa.Column(
            "object_type",
            sa.Enum(
                "memory",
                "kb_entry",
                "kb_document",
                "kb_collection",
                "candidate",
                name="ref_object_type",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("object_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("external_id", sa.String(length=200), nullable=True),
        sa.Column(
            "sync_status",
            sa.Enum(
                "pending",
                "synced",
                "failed",
                "deleting",
                "deleted",
                name="sync_status",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("synced_version", sa.Integer(), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["namespace_id"], ["namespaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("object_type", "object_id", "provider"),
        sa.UniqueConstraint("provider", "namespace_id", "external_id"),
    )
    with op.batch_alter_table("external_refs", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_external_refs_namespace_id"), ["namespace_id"], unique=False
        )

    op.create_table(
        "jobs",
        sa.Column("namespace_id", sa.Uuid(), nullable=False),
        sa.Column(
            "type",
            sa.Enum(
                "sync.memory_upsert",
                "sync.memory_remove",
                "sync.kb_upsert",
                "sync.kb_remove",
                name="job_type",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "payload",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "running",
                "done",
                "failed",
                name="job_status",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("run_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["namespace_id"], ["namespaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.create_index("ix_jobs_claim", ["status", "run_after"], unique=False)
        batch_op.create_index(batch_op.f("ix_jobs_namespace_id"), ["namespace_id"], unique=False)

    op.create_table(
        "kb_collections",
        sa.Column("namespace_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "review_policy",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "default_risk",
            sa.Enum(
                "low",
                "medium",
                "high",
                name="collection_risk",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["namespace_id"], ["namespaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("namespace_id", "name"),
    )
    with op.batch_alter_table("kb_collections", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_kb_collections_namespace_id"), ["namespace_id"], unique=False
        )

    op.create_table(
        "retrieval_logs",
        sa.Column("namespace_id", sa.Uuid(), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column(
            "filters",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "results",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "latency_ms",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "feedback",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["namespace_id"], ["namespaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("retrieval_logs", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_retrieval_logs_namespace_id"), ["namespace_id"], unique=False
        )

    op.create_table(
        "candidates",
        sa.Column("namespace_id", sa.Uuid(), nullable=False),
        sa.Column(
            "target_type",
            sa.Enum(
                "memory",
                "kb_entry",
                name="candidate_target",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "op",
            sa.Enum(
                "ADD",
                "UPDATE",
                "DELETE",
                name="candidate_op",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("target_id", sa.Uuid(), nullable=True),
        sa.Column(
            "draft",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "risk_level",
            sa.Enum(
                "low",
                "medium",
                "high",
                name="candidate_risk",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "origin",
            sa.Enum(
                "extraction",
                "promote",
                "user_suggestion",
                "api",
                name="candidate_origin",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "stage",
            sa.Enum(
                "awaiting_user",
                "awaiting_review",
                name="candidate_stage",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "accepted",
                "rejected",
                "expired",
                name="candidate_status",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("requested_for_user", sa.String(length=200), nullable=True),
        sa.Column("decided_by", sa.String(length=200), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("source_episode_id", sa.Uuid(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["namespace_id"], ["namespaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_episode_id"],
            ["episodes.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("candidates", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_candidates_namespace_id"), ["namespace_id"], unique=False
        )

    op.create_table(
        "kb_documents",
        sa.Column("namespace_id", sa.Uuid(), nullable=False),
        sa.Column("collection_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("source_uri", sa.Text(), nullable=True),
        sa.Column("mime", sa.String(length=200), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "processing",
                "ready",
                "failed",
                name="doc_status",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "risk_level",
            sa.Enum(
                "low",
                "medium",
                "high",
                name="doc_risk",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["collection_id"], ["kb_collections.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["namespace_id"], ["namespaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("kb_documents", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_kb_documents_collection_id"), ["collection_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_kb_documents_namespace_id"), ["namespace_id"], unique=False
        )

    op.create_table(
        "kb_entries",
        sa.Column("namespace_id", sa.Uuid(), nullable=False),
        sa.Column("collection_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "draft",
                "active",
                "verified",
                "deprecated",
                "rejected",
                name="kb_status",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "risk_level",
            sa.Enum(
                "low",
                "medium",
                "high",
                name="kb_risk",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "origin",
            sa.Enum(
                "manual",
                "import",
                "promoted_memory",
                "user_suggestion",
                "extraction",
                name="kb_origin",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "author_type",
            sa.Enum(
                "ai",
                "human",
                name="kb_author",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "source_refs",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(length=200), nullable=True),
        sa.Column("verified_by", sa.String(length=200), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_by", sa.Uuid(), nullable=True),
        sa.Column("confidence", sa.Double(), nullable=True),
        sa.Column("hit_count", sa.Integer(), nullable=False),
        sa.Column("helpful_count", sa.Integer(), nullable=False),
        sa.Column("unhelpful_count", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "tags",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "metadata",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["collection_id"], ["kb_collections.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["namespace_id"], ["namespaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["superseded_by"],
            ["kb_entries.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("kb_entries", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_kb_entries_collection_id"), ["collection_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_kb_entries_namespace_id"), ["namespace_id"], unique=False
        )

    op.create_table(
        "memories",
        sa.Column("namespace_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.String(length=200), nullable=True),
        sa.Column("agent_id", sa.String(length=200), nullable=True),
        sa.Column("session_id", sa.String(length=200), nullable=True),
        sa.Column("memory_type", sa.String(length=64), nullable=False),
        sa.Column("key", sa.String(length=200), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "data",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "active",
                "superseded",
                "deleted",
                name="memory_status",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "origin",
            sa.Enum(
                "extraction",
                "direct",
                "import",
                name="memory_origin",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("pinned", sa.Boolean(), nullable=False),
        sa.Column("importance", sa.Double(), nullable=False),
        sa.Column("confidence", sa.Double(), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_by", sa.Uuid(), nullable=True),
        sa.Column("source_episode_id", sa.Uuid(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("access_count", sa.Integer(), nullable=False),
        sa.Column("last_accessed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "tags",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "metadata",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["namespace_id"], ["namespaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_episode_id"],
            ["episodes.id"],
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by"],
            ["memories.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("memories", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_memories_namespace_id"), ["namespace_id"], unique=False
        )
        batch_op.create_index(
            "ix_memories_scope", ["namespace_id", "user_id", "agent_id", "status"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("memories", schema=None) as batch_op:
        batch_op.drop_index("ix_memories_scope")
        batch_op.drop_index(batch_op.f("ix_memories_namespace_id"))

    op.drop_table("memories")
    with op.batch_alter_table("kb_entries", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_kb_entries_namespace_id"))
        batch_op.drop_index(batch_op.f("ix_kb_entries_collection_id"))

    op.drop_table("kb_entries")
    with op.batch_alter_table("kb_documents", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_kb_documents_namespace_id"))
        batch_op.drop_index(batch_op.f("ix_kb_documents_collection_id"))

    op.drop_table("kb_documents")
    with op.batch_alter_table("candidates", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_candidates_namespace_id"))

    op.drop_table("candidates")
    with op.batch_alter_table("retrieval_logs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_retrieval_logs_namespace_id"))

    op.drop_table("retrieval_logs")
    with op.batch_alter_table("kb_collections", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_kb_collections_namespace_id"))

    op.drop_table("kb_collections")
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_jobs_namespace_id"))
        batch_op.drop_index("ix_jobs_claim")

    op.drop_table("jobs")
    with op.batch_alter_table("external_refs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_external_refs_namespace_id"))

    op.drop_table("external_refs")
    with op.batch_alter_table("episodes", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_episodes_namespace_id"))

    op.drop_table("episodes")
    with op.batch_alter_table("change_log", schema=None) as batch_op:
        batch_op.drop_index("ix_change_log_object")
        batch_op.drop_index(batch_op.f("ix_change_log_namespace_id"))

    op.drop_table("change_log")
    with op.batch_alter_table("api_keys", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_api_keys_namespace_id"))

    op.drop_table("api_keys")
    op.drop_table("namespaces")
    op.drop_table("admin_users")
