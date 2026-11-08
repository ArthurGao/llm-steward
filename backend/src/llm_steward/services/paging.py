"""Keyset pages over governance tables: newest first, `cursor` = the last id of the previous page
(ids are UUIDv7, so id order is creation order)."""

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

MAX_PAGE = 200


async def page[T](
    s: AsyncSession, query: Any, model: Any, cursor: uuid.UUID | None, limit: int
) -> tuple[list[T], uuid.UUID | None]:
    """One page of `query` (a select of `model`), and the cursor of the next (None: last page)."""
    limit = min(limit, MAX_PAGE)
    if cursor is not None:
        query = query.where(model.id < cursor)
    rows: list[T] = list(await s.scalars(query.order_by(model.id.desc()).limit(limit + 1)))
    items = rows[:limit]
    return items, (getattr(items[-1], "id") if len(rows) > limit else None)  # noqa: B009
