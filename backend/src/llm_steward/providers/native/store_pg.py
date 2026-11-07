"""PostgreSQL: pgvector (HNSW, cosine) + tsvector (`simple` config) over jieba tokens.

DDL names the governance schema explicitly, so a missing schema is an error from PostgreSQL itself
instead of a silent fall-through along the search_path into `public`.
"""

from sqlalchemy import text

from llm_steward.providers.native.store import (
    COLUMNS,
    NativeItem,
    NativeStore,
    StoredItem,
    item_params,
    row_to_item,
    scope_clause,
)


def _vector(embedding: list[float]) -> str:
    return "[" + ",".join(repr(float(x)) for x in embedding) + "]"


def _tsquery(tokens: list[str]) -> str:
    # tokens are \w+ only (see text.tokens), so no tsquery operators can be injected.
    return " | ".join(dict.fromkeys(tokens))


class PgNativeStore(NativeStore):
    def _ddl(self) -> list[str]:
        s = f'"{self._schema}"'  # validated identifier (Settings.db_schema)
        return [
            # pgvector lives in public (on the search_path), shared with mem0; never in our schema.
            "CREATE EXTENSION IF NOT EXISTS vector SCHEMA public",
            f"CREATE TABLE IF NOT EXISTS {s}.native_meta"
            " (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
            f"CREATE TABLE IF NOT EXISTS {s}.native_items ("
            " id BIGSERIAL PRIMARY KEY, part TEXT NOT NULL, kind TEXT NOT NULL,"
            " external_id TEXT NOT NULL, text TEXT NOT NULL, tokens TEXT NOT NULL,"
            " user_id TEXT, agent_id TEXT, session_id TEXT,"
            " metadata JSONB NOT NULL DEFAULT '{}',"
            f" embedding vector({self._dim}) NOT NULL, tsv tsvector NOT NULL,"
            " UNIQUE (kind, external_id))",
            f"CREATE INDEX IF NOT EXISTS ix_native_items_part ON {s}.native_items (part)",
            "CREATE INDEX IF NOT EXISTS ix_native_items_embedding"
            f" ON {s}.native_items USING hnsw (embedding vector_cosine_ops)",
            f"CREATE INDEX IF NOT EXISTS ix_native_items_tsv ON {s}.native_items USING gin (tsv)",
        ]

    async def upsert(self, item: NativeItem, embedding: list[float], tokens: list[str]) -> bool:
        values = {**item_params(item, tokens), "emb": _vector(embedding)}
        sql = (
            "INSERT INTO native_items (part, kind, external_id, text, tokens, user_id, agent_id,"
            " session_id, metadata, embedding, tsv)"
            " VALUES (:part, :kind, :eid, :text, :tokens, :user_id, :agent_id, :session_id,"
            " CAST(:metadata AS jsonb), CAST(:emb AS vector), to_tsvector('simple', :tokens))"
            " ON CONFLICT (kind, external_id) DO UPDATE SET part = EXCLUDED.part,"
            " text = EXCLUDED.text, tokens = EXCLUDED.tokens, user_id = EXCLUDED.user_id,"
            " agent_id = EXCLUDED.agent_id, session_id = EXCLUDED.session_id,"
            " metadata = EXCLUDED.metadata, embedding = EXCLUDED.embedding, tsv = EXCLUDED.tsv"
            " RETURNING (xmax <> 0) AS existed"
        )
        async with self._write() as conn:
            return bool(await conn.scalar(text(sql), values))

    async def delete(self, kind: str, external_id: str) -> None:
        async with self._write() as conn:
            await conn.execute(
                text("DELETE FROM native_items WHERE kind = :k AND external_id = :e"),
                {"k": kind, "e": external_id},
            )

    async def vector_search(
        self, part: str, embedding: list[float], limit: int, scope: dict[str, str]
    ) -> list[StoredItem]:
        where, params = scope_clause(scope, "i")
        sql = (
            f"SELECT {COLUMNS} FROM native_items i WHERE i.part = :part{where}"
            " ORDER BY i.embedding <=> CAST(:q AS vector) LIMIT :limit"
        )
        async with self._write() as conn:  # a transaction, for SET LOCAL
            # Keep scanning the HNSW graph until enough rows pass the filters (pgvector >= 0.8).
            await conn.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
            rows = await conn.execute(
                text(sql), {"q": _vector(embedding), "part": part, "limit": limit, **params}
            )
            return [row_to_item(r) for r in rows]

    async def text_search(
        self, part: str, tokens: list[str], limit: int, scope: dict[str, str]
    ) -> list[StoredItem]:
        if not tokens:
            return []
        where, params = scope_clause(scope, "i")
        sql = (
            f"SELECT {COLUMNS} FROM native_items i, to_tsquery('simple', :tq) q"
            f" WHERE i.part = :part AND i.tsv @@ q{where}"
            " ORDER BY ts_rank_cd(i.tsv, q) DESC LIMIT :limit"
        )
        async with self._read() as conn:
            rows = await conn.execute(
                text(sql), {"tq": _tsquery(tokens), "part": part, "limit": limit, **params}
            )
            return [row_to_item(r) for r in rows]
