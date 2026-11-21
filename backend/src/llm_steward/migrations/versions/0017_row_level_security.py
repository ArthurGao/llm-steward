"""P12: row-level security on PostgreSQL, the second line of tenant isolation (design §4).

Every table holding a namespace's rows gets ENABLE + FORCE (FORCE: the table owner, which
llm-steward and its tests connect as, is held to the policy too) and one policy: a transaction
sees and writes the rows of the namespace in `app.namespace_id`, or every row when
`app.rls_bypass` is 'on' (set only by `storage.db.unscoped()` and by migrations). With neither
set nothing matches: fail-closed. `namespaces` and `api_keys` stay without a policy:
authentication reads them before the namespace is known. SQLite has no row-level security.

A superuser or a BYPASSRLS role is not held to any policy: `STEWARD_REQUIRE_RLS=true` refuses
to start under one.

Hand-written and frozen (no imports from llm_steward).

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-22
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = (
    "episodes",
    "memories",
    "kb_collections",
    "kb_entries",
    "kb_documents",
    "kb_document_files",
    "candidates",
    "external_refs",
    "change_log",
    "retrieval_logs",
    "retrieval_feedback",
    "jobs",
    "idempotency_keys",
    "reconcile_findings",
)
VISIBLE = (
    "current_setting('app.rls_bypass', true) = 'on'"
    " OR namespace_id = nullif(current_setting('app.namespace_id', true), '')::uuid"
)


def upgrade() -> None:
    if op.get_context().dialect.name != "postgresql":
        return
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY ns_isolation ON {table} USING ({VISIBLE}) WITH CHECK ({VISIBLE})"
        )


def downgrade() -> None:
    if op.get_context().dialect.name != "postgresql":
        return
    for table in reversed(TABLES):
        op.execute(f"DROP POLICY IF EXISTS ns_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
