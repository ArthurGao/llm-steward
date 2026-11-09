"""P4e retrieval statistics: candidates left out of a retrieval and whether its hits are counted
(`retrieval_logs.left_out`, `counted`), and one row per feedback (`retrieval_feedback`). Hit
counts are rolled up from the logs in batches; no job per retrieval.

Hand-written and frozen (no imports from llm_steward). Logs written before this revision are
marked counted: their hits were never meant to be counted retroactively.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    with op.batch_alter_table("retrieval_logs") as batch:
        batch.add_column(sa.Column("left_out", JSON, nullable=False, server_default="[]"))
        batch.add_column(
            sa.Column("counted", sa.Boolean(), nullable=False, server_default=sa.true())
        )
    with op.batch_alter_table("retrieval_logs") as batch:
        batch.alter_column("left_out", server_default=None)
        batch.alter_column("counted", server_default=None)
        batch.create_index("ix_retrieval_logs_uncounted", ["counted", "id"])
    op.create_table(
        "retrieval_feedback",
        sa.Column("namespace_id", sa.Uuid(), nullable=False),
        sa.Column("retrieval_id", sa.Uuid(), nullable=False),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("helpful", sa.Boolean(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("given_by", sa.String(length=200), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["namespace_id"], ["namespaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["retrieval_id"], ["retrieval_logs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("retrieval_feedback") as batch:
        batch.create_index("ix_retrieval_feedback_namespace_id", ["namespace_id"])
        batch.create_index("ix_retrieval_feedback_retrieval_id", ["retrieval_id"])
        batch.create_index("ix_retrieval_feedback_recent", ["namespace_id", "created_at"])


def downgrade() -> None:
    op.drop_table("retrieval_feedback")
    with op.batch_alter_table("retrieval_logs") as batch:
        batch.drop_index("ix_retrieval_logs_uncounted")
        batch.drop_column("counted")
        batch.drop_column("left_out")
