"""P7d: what a reviewer had seen. `kb_entries.reviewed_{hits,helpful,unhelpful}` hold the counters
at the last review (a new version or a verification), so the daily feedback check counts only
what came after: an entry is not flagged again for the feedback the reviewer already looked at.

Hand-written and frozen (no imports from llm_steward).

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMNS = ("reviewed_hits", "reviewed_helpful", "reviewed_unhelpful")


def upgrade() -> None:
    with op.batch_alter_table("kb_entries") as batch:
        for name in COLUMNS:
            batch.add_column(sa.Column(name, sa.Integer(), nullable=False, server_default="0"))
    with op.batch_alter_table("kb_entries") as batch:
        for name in COLUMNS:
            batch.alter_column(name, server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("kb_entries") as batch:
        for name in COLUMNS:
            batch.drop_column(name)
