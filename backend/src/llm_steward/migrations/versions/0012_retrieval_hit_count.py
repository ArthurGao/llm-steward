"""P6a: `retrieval_logs.hit_count` (how many items a retrieval returned; 0 is a KB gap), so the
Admin can find gaps in SQL. Backfilled from each log's `results`.

Hand-written and frozen (no imports from llm_steward).

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-11
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("retrieval_logs") as batch:
        batch.add_column(sa.Column("hit_count", sa.Integer(), nullable=False, server_default="0"))
    with op.batch_alter_table("retrieval_logs") as batch:
        batch.alter_column("hit_count", server_default=None)
        batch.create_index("ix_retrieval_logs_gaps", ["namespace_id", "hit_count", "id"])
    bind = op.get_bind()
    logs = sa.table("retrieval_logs", sa.column("id"), sa.column("results"), sa.column("hit_count"))
    for log_id, results in bind.execute(sa.select(logs.c.id, logs.c.results)).all():
        rows = json.loads(results) if isinstance(results, str) else (results or [])
        if rows:
            bind.execute(sa.update(logs).where(logs.c.id == log_id).values(hit_count=len(rows)))


def downgrade() -> None:
    with op.batch_alter_table("retrieval_logs") as batch:
        batch.drop_index("ix_retrieval_logs_gaps")
        batch.drop_column("hit_count")
