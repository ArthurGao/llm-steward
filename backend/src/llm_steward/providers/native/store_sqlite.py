"""SQLite: sqlite-vec `vec0` (partitioned per namespace/collection) + FTS5 over jieba tokens."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from llm_steward.providers.native.store import (
    COLUMNS,
    NativeItem,
    NativeStore,
    StoredItem,
    item_params,
    row_to_item,
    scope_clause,
)

# vec0 returns the k nearest within the partition before scope filtering, so over-fetch.
_KNN_OVERFETCH = 10
_KNN_MAX = 1000


def _vector(embedding: list[float]) -> bytes:
    import sqlite_vec

    return bytes(sqlite_vec.serialize_float32(embedding))


def _fts_query(tokens: list[str]) -> str:
    # tokens are \w+ only (see text.tokens); quoting still guards FTS5 keywords such as OR/NEAR.
    return " OR ".join('"' + t.replace('"', '""') + '"' for t in dict.fromkeys(tokens))


async def _drop_indexes(conn: AsyncConnection, row_id: int) -> None:
    for table in ("native_vec", "native_fts"):
        await conn.execute(text(f"DELETE FROM {table} WHERE rowid = :id"), {"id": row_id})


class SqliteNativeStore(NativeStore):
    def _ddl(self) -> list[str]:
        return [
            "CREATE TABLE IF NOT EXISTS native_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS native_items ("
            " id INTEGER PRIMARY KEY, part TEXT NOT NULL, kind TEXT NOT NULL,"
            " external_id TEXT NOT NULL, text TEXT NOT NULL, tokens TEXT NOT NULL,"
            " user_id TEXT, agent_id TEXT, session_id TEXT,"
            " metadata TEXT NOT NULL DEFAULT '{}', UNIQUE (kind, external_id))",
            "CREATE INDEX IF NOT EXISTS ix_native_items_part ON native_items (part)",
            "CREATE VIRTUAL TABLE IF NOT EXISTS native_vec USING vec0("
            f" part text partition key, embedding float[{self._dim}] distance_metric=cosine)",
            "CREATE VIRTUAL TABLE IF NOT EXISTS native_fts USING fts5(tokens, part UNINDEXED)",
        ]

    async def upsert(self, item: NativeItem, embedding: list[float], tokens: list[str]) -> bool:
        values = item_params(item, tokens)
        async with self._write() as conn:
            existing = await conn.scalar(
                text("SELECT id FROM native_items WHERE kind = :kind AND external_id = :eid"),
                values,
            )
            if existing is None:
                row_id = await conn.scalar(
                    text(
                        "INSERT INTO native_items (part, kind, external_id, text, tokens,"
                        " user_id, agent_id, session_id, metadata)"
                        " VALUES (:part, :kind, :eid, :text, :tokens, :user_id, :agent_id,"
                        " :session_id, :metadata) RETURNING id"
                    ),
                    values,
                )
            else:
                row_id = existing
                await conn.execute(
                    text(
                        "UPDATE native_items SET part = :part, text = :text, tokens = :tokens,"
                        " user_id = :user_id, agent_id = :agent_id, session_id = :session_id,"
                        " metadata = :metadata WHERE id = :id"
                    ),
                    {**values, "id": row_id},
                )
                await _drop_indexes(conn, row_id)
            await conn.execute(
                text("INSERT INTO native_vec (rowid, part, embedding) VALUES (:id, :part, :emb)"),
                {"id": row_id, "part": item.part, "emb": _vector(embedding)},
            )
            await conn.execute(
                text("INSERT INTO native_fts (rowid, tokens, part) VALUES (:id, :tokens, :part)"),
                {"id": row_id, "tokens": values["tokens"], "part": item.part},
            )
        return existing is not None

    async def delete(self, kind: str, external_id: str) -> None:
        async with self._write() as conn:
            row_id = await conn.scalar(
                text("DELETE FROM native_items WHERE kind = :k AND external_id = :e RETURNING id"),
                {"k": kind, "e": external_id},
            )
            if row_id is not None:
                await _drop_indexes(conn, row_id)

    async def vector_search(
        self, part: str, embedding: list[float], limit: int, scope: dict[str, str]
    ) -> list[StoredItem]:
        where, params = scope_clause(scope, "i")
        sql = (
            "WITH knn AS (SELECT rowid, distance FROM native_vec"
            " WHERE embedding MATCH :q AND k = :k AND part = :part)"
            f" SELECT {COLUMNS} FROM knn JOIN native_items i ON i.id = knn.rowid"
            f" WHERE 1 = 1{where} ORDER BY knn.distance LIMIT :limit"
        )
        k = min(limit * _KNN_OVERFETCH, _KNN_MAX)
        async with self._read() as conn:
            rows = await conn.execute(
                text(sql),
                {"q": _vector(embedding), "k": k, "part": part, "limit": limit, **params},
            )
            return [row_to_item(r) for r in rows]

    async def text_search(
        self, part: str, tokens: list[str], limit: int, scope: dict[str, str]
    ) -> list[StoredItem]:
        if not tokens:
            return []
        where, params = scope_clause(scope, "i")
        sql = (
            f"SELECT {COLUMNS} FROM native_fts f JOIN native_items i ON i.id = f.rowid"
            f" WHERE native_fts MATCH :m AND f.part = :part{where}"
            " ORDER BY bm25(native_fts) LIMIT :limit"
        )
        async with self._read() as conn:
            rows = await conn.execute(
                text(sql), {"m": _fts_query(tokens), "part": part, "limit": limit, **params}
            )
            return [row_to_item(r) for r in rows]
