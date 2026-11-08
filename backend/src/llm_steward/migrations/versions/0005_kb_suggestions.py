"""P3b KB suggestions: a card remembers its conversation (capped per session), the kb.suggest job
(scoring new memories for the KB), and an index for the daily expiry sweep.

Hand-written and frozen (no imports from llm_steward). The jobs CHECK constraint is dropped and
recreated by name through a batch operation (a table rebuild on SQLite, in place on PostgreSQL).

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JOB_TYPES_0003 = (
    "sync.memory_upsert",
    "sync.memory_remove",
    "sync.kb_upsert",
    "sync.kb_remove",
    "memory.extract",
    "memory.reconcile",
)
JOB_TYPES = (*JOB_TYPES_0003, "kb.suggest")


def _job_types(values: Sequence[str]) -> None:
    with op.batch_alter_table("jobs") as batch:
        batch.drop_constraint("job_type", type_="check")
        batch.create_check_constraint("job_type", f"type IN ({', '.join(map(repr, values))})")


def upgrade() -> None:
    with op.batch_alter_table("candidates") as batch:
        batch.add_column(sa.Column("session_id", sa.String(200), nullable=True))
    op.create_index("ix_candidates_expiry", "candidates", ["status", "expires_at"])
    _job_types(JOB_TYPES)


def downgrade() -> None:
    _job_types(JOB_TYPES_0003)
    op.drop_index("ix_candidates_expiry", table_name="candidates")
    with op.batch_alter_table("candidates") as batch:
        batch.drop_column("session_id")
