"""The two scope predicates every scoped query uses, so their NULL rules live in one place.

- `exact`: the same scope slot. NULL compares equal to NULL through COALESCE — the form the
  `coalesce(...)` expression indexes (profile keys, the episode buffer) are built on.
- `visible`: what a caller with this scope may see — its own rows or shared ones (NULL); a caller
  without a user only sees shared rows.
"""

from typing import Any

from sqlalchemy import ColumnElement, func, or_

from llm_steward.providers.base import Scope


def _columns(model: Any, scope: Scope, session: bool) -> list[tuple[Any, str | None]]:
    pairs = [(model.user_id, scope.user_id), (model.agent_id, scope.agent_id)]
    if session:
        pairs.append((model.session_id, scope.session_id))
    return pairs


def exact(model: Any, scope: Scope, *, session: bool) -> tuple[ColumnElement[bool], ...]:
    return (
        model.namespace_id == scope.namespace_id,
        *(
            func.coalesce(col, "") == (value or "")
            for col, value in _columns(model, scope, session)
        ),
    )


def visible(model: Any, scope: Scope, *, session: bool) -> tuple[ColumnElement[bool], ...]:
    return (
        model.namespace_id == scope.namespace_id,
        *(
            or_(col.is_(None), col == value) if value else col.is_(None)
            for col, value in _columns(model, scope, session)
        ),
    )
