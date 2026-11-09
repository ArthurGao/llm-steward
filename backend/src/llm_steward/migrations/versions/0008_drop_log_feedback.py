"""P4 close-out: drop `retrieval_logs.feedback`. Feedback has its own table since 0007
(`retrieval_feedback`); the JSON column was never written after that and would be mistaken for
live data.

Hand-written and frozen (no imports from llm_steward).

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    with op.batch_alter_table("retrieval_logs") as batch:
        batch.drop_column("feedback")


def downgrade() -> None:
    with op.batch_alter_table("retrieval_logs") as batch:
        batch.add_column(sa.Column("feedback", JSON, nullable=True))
