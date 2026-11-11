"""P7 close-out: a user's "stop suggesting" rule moves to the reserved key space. It was the
profile key `stop_suggestions`, which a host app could also write (and so silence a user's
cards by accident); llm-steward now writes `steward.stop_suggestions`, which no client may set,
and matches procedural memories only. Rules written before (procedural, the old key) move over.

Hand-written and frozen (no imports from llm_steward).

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD, NEW = "stop_suggestions", "steward.stop_suggestions"
memories = sa.table("memories", sa.column("key", sa.String), sa.column("memory_type", sa.String))


def _rename(old: str, new: str) -> None:
    op.execute(
        sa.update(memories)
        .where(memories.c.key == old, memories.c.memory_type == "procedural")
        .values(key=new)
    )


def upgrade() -> None:
    _rename(OLD, NEW)


def downgrade() -> None:
    _rename(NEW, OLD)
