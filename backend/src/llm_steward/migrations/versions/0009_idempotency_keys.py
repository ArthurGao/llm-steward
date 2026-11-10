"""P5b: `idempotency_keys`, one row per write sent with an `Idempotency-Key` header (design §7),
kept 24 hours; a retry with the same key replays the stored response.

Hand-written and frozen (no imports from llm_steward).

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "idempotency_keys",
        sa.Column("namespace_id", sa.Uuid(), nullable=False),
        sa.Column("principal", sa.String(length=300), nullable=False),
        sa.Column("key", sa.String(length=200), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("content_type", sa.String(length=200), nullable=True),
        sa.Column("body", sa.LargeBinary(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["namespace_id"], ["namespaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("namespace_id", "principal", "key"),
    )
    with op.batch_alter_table("idempotency_keys") as batch:
        batch.create_index("ix_idempotency_keys_namespace_id", ["namespace_id"])
        batch.create_index("ix_idempotency_keys_created", ["created_at"])


def downgrade() -> None:
    op.drop_table("idempotency_keys")
