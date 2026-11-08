"""P2 memory pipeline: host_app actor, episode objects, extract/reconcile jobs, one active value per
profile key, the episode-buffer and supersede-chain indexes. (mem0's own schema is created by the
mem0 provider, which owns that storage.)

Hand-written and frozen (no imports from llm_steward). CHECK constraints are dropped and recreated
by name through batch operations, which rebuild the table on SQLite and alter it in place on
PostgreSQL.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OBJECT_TYPES_0002 = ("memory", "kb_entry", "kb_document", "kb_collection", "candidate")
OBJECT_TYPES = (*OBJECT_TYPES_0002, "episode")
ACTOR_TYPES_0002 = ("ai", "end_user", "admin", "system", "provider")
ACTOR_TYPES = (*ACTOR_TYPES_0002, "host_app")
JOB_TYPES_0002 = ("sync.memory_upsert", "sync.memory_remove", "sync.kb_upsert", "sync.kb_remove")
JOB_TYPES = (*JOB_TYPES_0002, "memory.extract", "memory.reconcile")

# (table, constraint name, column, values before, values after)
CHECKS = [
    ("change_log", "actor_type", "actor_type", ACTOR_TYPES_0002, ACTOR_TYPES),
    ("change_log", "log_object_type", "object_type", OBJECT_TYPES_0002, OBJECT_TYPES),
    ("external_refs", "ref_object_type", "object_type", OBJECT_TYPES_0002, OBJECT_TYPES),
    ("jobs", "job_type", "type", JOB_TYPES_0002, JOB_TYPES),
]

PROFILE_KEY_INDEX = "uq_memories_active_profile_key"
PROFILE_KEY_WHERE = "status = 'active' AND key IS NOT NULL"
BUFFER_INDEX = "ix_episodes_buffer"
BUFFER_WHERE = "processed_at IS NULL"
SUPERSEDED_BY_INDEX = "ix_memories_superseded_by"


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _set_checks(after: bool) -> None:
    for table, name, column, before_values, after_values in CHECKS:
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(name, type_="check")
            batch.create_check_constraint(
                name, _in(column, after_values if after else before_values)
            )


def upgrade() -> None:
    _set_checks(after=True)
    where = sa.text(PROFILE_KEY_WHERE)
    op.create_index(
        PROFILE_KEY_INDEX,
        "memories",
        [
            "namespace_id",
            sa.text("coalesce(user_id, '')"),
            sa.text("coalesce(agent_id, '')"),
            "key",
        ],
        unique=True,
        postgresql_where=where,
        sqlite_where=where,
    )
    unprocessed = sa.text(BUFFER_WHERE)
    op.create_index(
        BUFFER_INDEX,
        "episodes",
        [
            "namespace_id",
            sa.text("coalesce(user_id, '')"),
            sa.text("coalesce(agent_id, '')"),
            sa.text("coalesce(session_id, '')"),
            "created_at",
        ],
        postgresql_where=unprocessed,
        sqlite_where=unprocessed,
    )
    op.create_index(SUPERSEDED_BY_INDEX, "memories", ["superseded_by"])


def downgrade() -> None:
    op.drop_index(SUPERSEDED_BY_INDEX, table_name="memories")
    op.drop_index(BUFFER_INDEX, table_name="episodes")
    op.drop_index(PROFILE_KEY_INDEX, table_name="memories")
    _set_checks(after=False)
