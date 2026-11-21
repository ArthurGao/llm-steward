"""P13: a person decides on the reconcile triage assistant's proposals. `reconcile_findings`
gets the decision (applied / dismissed), when and by whom; the job type that brings an
unmirrored record under governance (`memory.reconcile_remirror`) joins the jobs CHECK. A
remirrored memory's origin is `import` (taken in from the engine): a new origin would rebuild
`memories` on SQLite, which drops its partial indexes.

Hand-written and frozen (no imports from llm_steward).

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-22
"""

import warnings
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JOB_TYPES_0016 = (
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
    "memory.reconcile_triage",
)
JOB_TYPES = (*JOB_TYPES_0016, "memory.reconcile_remirror")

# (table, constraint, column, before, after)
CHECKS = [
    ("jobs", "job_type", "type", JOB_TYPES_0016, JOB_TYPES),
]


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _set_checks(after: bool) -> None:
    # SQLite rebuilds the tables; the expression-index reflection warning is about other tables.
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore", message="Skipped unsupported reflection of expression-based index"
        )
        for table, name, column, before, now in CHECKS:
            with op.batch_alter_table(table) as batch:
                batch.drop_constraint(name, type_="check")
                batch.create_check_constraint(name, _in(column, now if after else before))


def upgrade() -> None:
    _set_checks(after=True)
    with op.batch_alter_table("reconcile_findings") as batch:
        batch.add_column(sa.Column("decision", sa.String(length=32), nullable=True))
        batch.add_column(sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("decided_by", sa.String(length=200), nullable=True))
    with op.batch_alter_table("reconcile_findings") as batch:
        batch.create_check_constraint("finding_decision", "decision IN ('applied', 'dismissed')")


def downgrade() -> None:
    with op.batch_alter_table("reconcile_findings") as batch:
        batch.drop_constraint("finding_decision", type_="check")
    with op.batch_alter_table("reconcile_findings") as batch:
        batch.drop_column("decided_by")
        batch.drop_column("decided_at")
        batch.drop_column("decision")
    op.execute("DELETE FROM jobs WHERE type = 'memory.reconcile_remirror'")
    _set_checks(after=False)
