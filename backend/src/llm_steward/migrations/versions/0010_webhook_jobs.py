"""P5c: the `webhook.deliver` job type (webhooks are delivered by the outbox worker).

Hand-written and frozen (no imports from llm_steward).

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-10
"""

import warnings
from collections.abc import Sequence

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JOB_TYPES_0006 = (
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
)
JOB_TYPES = (*JOB_TYPES_0006, "webhook.deliver")


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


def downgrade() -> None:
    op.execute(f"DELETE FROM jobs WHERE type NOT IN ({', '.join(map(repr, JOB_TYPES_0006))})")
    _job_types(JOB_TYPES_0006)
