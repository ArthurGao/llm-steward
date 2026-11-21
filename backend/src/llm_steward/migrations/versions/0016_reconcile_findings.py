"""P11: `reconcile_findings`, the reconcile triage assistant's proposals for provider records no
mirror row points at (design §11 P11; proposals only: nothing applies them), and its job type
`memory.reconcile_triage`.

Hand-written and frozen (no imports from llm_steward).

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-22
"""

import warnings
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JOB_TYPES_0011 = (
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
    "webhook.deliver",
    "kb.doc_fetch",
)
JOB_TYPES = (*JOB_TYPES_0011, "memory.reconcile_triage")


def _job_types(values: Sequence[str]) -> None:
    # SQLite rebuilds `jobs`; the expression-index reflection warning is about other tables.
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore", message="Skipped unsupported reflection of expression-based index"
        )
        with op.batch_alter_table("jobs") as batch:
            batch.drop_constraint("job_type", type_="check")
            batch.create_check_constraint("job_type", f"type IN ({', '.join(map(repr, values))})")


def upgrade() -> None:
    _job_types(JOB_TYPES)
    op.create_table(
        "reconcile_findings",
        sa.Column("namespace_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("external_id", sa.String(length=200), nullable=False),
        sa.Column("diagnosis", sa.Text(), nullable=False),
        sa.Column(
            "proposed_action",
            sa.Enum(
                "ignore",
                "remirror",
                "erase",
                name="triage_action",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "evidence",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("model", sa.String(length=200), nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["namespace_id"], ["namespaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("namespace_id", "provider", "external_id", name="uq_reconcile_finding"),
    )
    with op.batch_alter_table("reconcile_findings") as batch:
        batch.create_index(
            batch.f("ix_reconcile_findings_namespace_id"), ["namespace_id"], unique=False
        )
        batch.create_index("ix_reconcile_findings_recent", ["namespace_id", "created_at"])


def downgrade() -> None:
    op.drop_table("reconcile_findings")
    op.execute(f"DELETE FROM jobs WHERE type NOT IN ({', '.join(map(repr, JOB_TYPES_0011))})")
    _job_types(JOB_TYPES_0011)
