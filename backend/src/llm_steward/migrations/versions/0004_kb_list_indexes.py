"""P3 KB governance: indexes for the review queue, end users' cards and the entry list.

Hand-written and frozen (no imports from llm_steward).

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-09
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEXES = [
    ("ix_kb_entries_list", "kb_entries", ["namespace_id", "status", "id"]),
    ("ix_candidates_queue", "candidates", ["namespace_id", "status", "stage", "id"]),
    ("ix_candidates_user", "candidates", ["namespace_id", "requested_for_user", "status", "id"]),
]


def upgrade() -> None:
    for name, table, columns in INDEXES:
        op.create_index(name, table, columns)


def downgrade() -> None:
    for name, table, _ in reversed(INDEXES):
        op.drop_index(name, table_name=table)
