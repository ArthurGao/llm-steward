"""P3d KB documents: a document's governed state and upload facts, the bytes waiting for the
worker (`kb_document_files`), document candidates, and the document sync jobs.

Hand-written and frozen (no imports from llm_steward). CHECK constraints are dropped and recreated
by name through batch operations (a table rebuild on SQLite, in place on PostgreSQL). Nothing wrote
`kb_documents` before this revision; the server defaults only cover rows that should not exist.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-09
"""

import warnings
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JOB_TYPES_0005 = (
    "sync.memory_upsert",
    "sync.memory_remove",
    "sync.kb_upsert",
    "sync.kb_remove",
    "memory.extract",
    "memory.reconcile",
    "kb.suggest",
)
JOB_TYPES = (*JOB_TYPES_0005, "kb.doc_ingest", "kb.doc_status", "kb.doc_remove")
TARGETS_0005 = ("memory", "kb_entry")
TARGETS = (*TARGETS_0005, "kb_document")
DOC_STATES = ("draft", "active", "verified", "deprecated", "rejected")


def _check(table: str, name: str, column: str, values: Sequence[str]) -> None:
    # SQLite rebuilds the table and reflects the tables its foreign keys point at; `candidates`
    # points at `episodes`, whose expression index SQLite reflection cannot read. That index is
    # not part of this rebuild, so the warning (once per migrated database) says nothing here.
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore", message="Skipped unsupported reflection of expression-based index"
        )
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(name, type_="check")
            batch.create_check_constraint(name, f"{column} IN ({', '.join(map(repr, values))})")


def upgrade() -> None:
    state = sa.Enum(
        *DOC_STATES, name="doc_state", native_enum=False, create_constraint=True, length=32
    )
    with op.batch_alter_table("kb_documents") as batch:
        batch.add_column(sa.Column("state", state, nullable=False, server_default="rejected"))
        batch.add_column(sa.Column("filename", sa.String(500), nullable=False, server_default=""))
        batch.add_column(sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("created_by", sa.String(200), nullable=True))
        batch.add_column(sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("error", sa.String(200), nullable=True))
    with op.batch_alter_table("kb_documents") as batch:
        for column in ("state", "filename", "size_bytes"):
            batch.alter_column(column, server_default=None)
        batch.create_index("ix_kb_documents_list", ["namespace_id", "state", "id"])
        batch.create_index("ix_kb_documents_hash", ["collection_id", "content_hash"])
    op.create_table(
        "kb_document_files",
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("namespace_id", sa.Uuid(), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["kb_documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["namespace_id"], ["namespaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("document_id"),
    )
    op.create_index(
        "ix_kb_document_files_namespace_id", "kb_document_files", ["namespace_id"], unique=False
    )
    _check("candidates", "candidate_target", "target_type", TARGETS)
    _check("jobs", "job_type", "type", JOB_TYPES)


def downgrade() -> None:
    op.execute("DELETE FROM jobs WHERE type IN ('kb.doc_ingest', 'kb.doc_status', 'kb.doc_remove')")
    op.execute("DELETE FROM candidates WHERE target_type = 'kb_document'")
    _check("jobs", "job_type", "type", JOB_TYPES_0005)
    _check("candidates", "candidate_target", "target_type", TARGETS_0005)
    op.drop_index("ix_kb_document_files_namespace_id", table_name="kb_document_files")
    op.drop_table("kb_document_files")
    with op.batch_alter_table("kb_documents") as batch:
        batch.drop_index("ix_kb_documents_hash")
        batch.drop_index("ix_kb_documents_list")
        batch.drop_constraint("doc_state", type_="check")  # SQLite's rebuild would keep it
        for column in ("error", "valid_to", "created_by", "size_bytes", "filename", "state"):
            batch.drop_column(column)
