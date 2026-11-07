"""Storage behind the native provider: one items table plus a vector index and a full-text index.

The dialects differ only in DDL and in how vectors and full-text queries are written: see
`store_sqlite.py` (sqlite-vec + FTS5) and `store_pg.py` (pgvector + tsvector). These tables belong
to the provider, not to the governance migrations: they are created on first use (so a database
outage never stops the process from starting) and can be dropped and rebuilt on their own.
"""

import asyncio
import json
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from llm_steward.errors import ProviderUnavailable

SCOPE_COLUMNS = ("user_id", "agent_id", "session_id")
# Bump when the native tables change shape; an index built with another layout must be rebuilt.
LAYOUT_VERSION = "2"
COLUMNS = "i.external_id, i.text, i.tokens, i.user_id, i.agent_id, i.session_id, i.metadata"


@dataclass(frozen=True)
class NativeItem:
    part: str  # partition: one per namespace (memory) or per collection (kb)
    kind: str  # "memory" | "kb"
    external_id: str
    text: str
    user_id: str | None = None
    agent_id: str | None = None
    session_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StoredItem:
    external_id: str
    text: str
    tokens: list[str]  # the full-text tokens stored at write time
    user_id: str | None
    agent_id: str | None
    session_id: str | None
    metadata: dict[str, Any]


def scope_clause(scope: dict[str, str], alias: str) -> tuple[str, dict[str, str]]:
    """`(col = :v OR col IS NULL)` per given dimension: an item matches the caller's own scope or a
    shared one. Column names come from a fixed list, values are bound."""
    parts, params = [], {}
    for column, value in scope.items():
        if column not in SCOPE_COLUMNS:
            raise ValueError(f"unknown scope column {column!r}")
        parts.append(f"({alias}.{column} = :scope_{column} OR {alias}.{column} IS NULL)")
        params[f"scope_{column}"] = value
    return (" AND " + " AND ".join(parts)) if parts else "", params


def row_to_item(row: Any) -> StoredItem:
    metadata = row.metadata
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    return StoredItem(
        row.external_id,
        row.text,
        row.tokens.split(),
        row.user_id,
        row.agent_id,
        row.session_id,
        metadata or {},
    )


def item_params(item: NativeItem, tokens: list[str]) -> dict[str, Any]:
    return {
        "part": item.part,
        "kind": item.kind,
        "eid": item.external_id,
        "text": item.text,
        "tokens": " ".join(tokens),
        "user_id": item.user_id,
        "agent_id": item.agent_id,
        "session_id": item.session_id,
        "metadata": json.dumps(item.metadata),
    }


class NativeStore(ABC):
    def __init__(self, engine: AsyncEngine, model: str, dim: int, schema: str) -> None:
        self._engine = engine
        self._model = model
        self._dim = dim
        self._schema = schema
        self._ready = False
        self._ready_lock = asyncio.Lock()

    async def ready(self) -> None:
        """Create the tables once per process; raises ProviderUnavailable if they cannot exist."""
        if self._ready:
            return
        async with self._ready_lock:
            if not self._ready:
                await self._ensure_schema()
                self._ready = True

    @asynccontextmanager
    async def _read(self) -> AsyncIterator[AsyncConnection]:
        await self.ready()
        async with self._engine.connect() as conn:
            yield conn

    @asynccontextmanager
    async def _write(self) -> AsyncIterator[AsyncConnection]:
        await self.ready()
        async with self._engine.begin() as conn:
            yield conn

    async def _ensure_schema(self) -> None:
        async with self._engine.begin() as conn:
            for statement in self._ddl():
                try:
                    await conn.execute(text(statement))
                except DBAPIError as exc:
                    raise ProviderUnavailable(
                        f"cannot create the native index ({type(exc.orig).__name__}); "
                        "if the governance schema is missing, run `steward migrate` first"
                    ) from exc
            await self._check_index_identity(conn)

    async def _check_index_identity(self, conn: AsyncConnection) -> None:
        """Vectors from another model, or rows in another layout, cannot be mixed in: refuse."""
        expected = {
            "embedding_model": self._model,
            "embedding_dim": str(self._dim),
            "layout": LAYOUT_VERSION,
        }
        result = await conn.execute(text("SELECT key, value FROM native_meta"))
        found: dict[str, str] = {k: v for k, v in result}
        if not found:
            for key, value in expected.items():
                await conn.execute(
                    text("INSERT INTO native_meta (key, value) VALUES (:k, :v)"),
                    {"k": key, "v": value},
                )
        elif found != expected:
            raise ProviderUnavailable(
                f"native index was built as {found} but {expected} is configured; "
                "rebuild the native index (drop the native_* tables) before switching"
            )

    async def get(self, kind: str, external_id: str) -> StoredItem | None:
        sql = f"SELECT {COLUMNS} FROM native_items i WHERE kind = :k AND external_id = :e"
        async with self._read() as conn:
            row = (await conn.execute(text(sql), {"k": kind, "e": external_id})).first()
        return row_to_item(row) if row else None

    async def list_items(self, part: str, scope: dict[str, str]) -> list[StoredItem]:
        where, params = scope_clause(scope, "i")
        sql = f"SELECT {COLUMNS} FROM native_items i WHERE i.part = :part{where} ORDER BY i.id"
        async with self._read() as conn:
            rows = await conn.execute(text(sql), {"part": part, **params})
            return [row_to_item(r) for r in rows]

    # dialect-specific

    @abstractmethod
    def _ddl(self) -> list[str]: ...

    @abstractmethod
    async def upsert(self, item: NativeItem, embedding: list[float], tokens: list[str]) -> bool:
        """Insert or replace by (kind, external_id); True if it existed."""

    @abstractmethod
    async def delete(self, kind: str, external_id: str) -> None: ...

    @abstractmethod
    async def vector_search(
        self, part: str, embedding: list[float], limit: int, scope: dict[str, str]
    ) -> list[StoredItem]: ...

    @abstractmethod
    async def text_search(
        self, part: str, tokens: list[str], limit: int, scope: dict[str, str]
    ) -> list[StoredItem]: ...
