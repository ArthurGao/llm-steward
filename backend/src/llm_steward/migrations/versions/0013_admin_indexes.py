"""P6 close-out: indexes for the Admin's last-7-days reads (`change_log`, `retrieval_logs` by
namespace and time), and the `admin_users` table dropped: the Admin has no login or accounts
(decided 2026-11-10; it uses the static STEWARD_ADMIN_TOKEN).

Hand-written and frozen (no imports from llm_steward).

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("change_log") as batch:
        batch.create_index("ix_change_log_recent", ["namespace_id", "created_at"])
    with op.batch_alter_table("retrieval_logs") as batch:
        batch.create_index("ix_retrieval_logs_recent", ["namespace_id", "created_at"])
    op.drop_table("admin_users")


def downgrade() -> None:
    op.create_table(
        "admin_users",
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.String(length=200), nullable=False),
        sa.Column(
            "role",
            sa.Enum(
                "owner",
                "reviewer",
                "viewer",
                name="admin_role",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "namespaces",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    with op.batch_alter_table("retrieval_logs") as batch:
        batch.drop_index("ix_retrieval_logs_recent")
    with op.batch_alter_table("change_log") as batch:
        batch.drop_index("ix_change_log_recent")
