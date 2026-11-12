"""mem0 (2.2.1, pinned) as a MemoryProvider: in-process, on our PostgreSQL in schema `mem0`.

- Telemetry is forced off and mem0's home dir (`MEM0_DIR`) moved into our data dir before mem0 is
  imported (both are read at import time).
- Namespace isolation: every record carries `namespace_id` in its metadata and every query filters
  on it. mem0 refuses a query without an entity id, so shared (user-less) memories are stored under
  the sentinel user `__shared__`; a search runs once for the user and once for the shared memories.
- The engine is built on first use (it loads the embedding model and creates its table), under a
  lock, so a failure shows up in /health instead of stopping the process.
"""

import asyncio
import os
import uuid
from typing import Any
from urllib.parse import quote

from llm_steward.errors import ProviderUnavailable
from llm_steward.llm.roles import Role
from llm_steward.providers.base import (
    DELETE,
    EXTRACT,
    UPDATE,
    ComponentHealth,
    MemoryEvent,
    MemoryRecord,
    Message,
    ProviderHit,
    Scope,
)
from llm_steward.storage.db import libpq_url

SHARED_USER = "__shared__"
LIST_LIMIT = 10_000
SCHEMA = "mem0"


def mem0_connection_string(database_url: str, schema: str) -> str:
    """The governance URL as a libpq URL whose search_path puts mem0's tables in `schema`
    (pgvector's types stay reachable in public)."""
    libpq = libpq_url(database_url)
    if libpq is None:
        raise ProviderUnavailable("the mem0 memory provider needs PostgreSQL (pgvector)")
    options = quote(f"-csearch_path={schema},public")
    return f"{libpq}{'&' if '?' in libpq else '?'}options={options}"


def build_mem0_config(
    *,
    embedding_model: str,
    embedding_dims: int,
    llm_model: str,
    llm_base_url: str,
    llm_api_key: str,
    history_db_path: str,
    collection: str = "memories",
) -> dict[str, Any]:
    return {
        "vector_store": {
            "provider": "pgvector",
            "config": {
                "collection_name": collection,
                "embedding_model_dims": embedding_dims,
                "hnsw": True,
            },
        },
        "embedder": {
            "provider": "fastembed",
            "config": {"model": embedding_model, "embedding_dims": embedding_dims},
        },
        "llm": {
            "provider": "openai",
            "config": {"model": llm_model, "openai_base_url": llm_base_url, "api_key": llm_api_key},
        },
        "history_db_path": history_db_path,
    }


def prepare_mem0_environment(history_db_path: str, model_cache_dir: str | None) -> None:
    """mem0 reads these once, when it is first imported, so this runs before that import.

    Telemetry off (it would send usage events to mem0's PostHog); MEM0_DIR keeps mem0's anonymous
    id file in our data dir instead of ~/.mem0; the embedding model goes to our cache.
    """
    data_dir = os.path.dirname(os.path.abspath(history_db_path))
    os.makedirs(data_dir, exist_ok=True)
    os.environ["MEM0_TELEMETRY"] = "false"
    os.environ.setdefault("MEM0_DIR", data_dir)
    if model_cache_dir:
        os.environ.setdefault("FASTEMBED_CACHE_PATH", model_cache_dir)


def _scope_kwargs(scope: Scope) -> dict[str, str]:
    kwargs = {"user_id": scope.user_id or SHARED_USER}
    if scope.agent_id:
        kwargs["agent_id"] = scope.agent_id
    if scope.session_id:
        kwargs["run_id"] = scope.session_id
    return kwargs


def _filters(scope: Scope, user: str | None) -> dict[str, str]:
    """mem0 filters for one entity; agent and session scoping is left to the governance safety
    net, which also admits shared rows (mem0 cannot express "own or shared")."""
    return {"user_id": user or SHARED_USER, "namespace_id": str(scope.namespace_id)}


class Mem0Provider:
    name = "mem0"
    # no METADATA_FILTER: search scopes by user/agent/session only (governance post-filters)
    capabilities = frozenset({EXTRACT, UPDATE, DELETE})

    def __init__(
        self,
        config: dict[str, Any],
        *,
        database_url: str,
        schema: str = SCHEMA,
        model_cache_dir: str | None = None,
    ) -> None:
        """`config` from `build_mem0_config`; the connection string (and with it the schema mem0
        writes to) is derived here from `schema`, so the two can never disagree."""
        self._config = {
            **config,
            "vector_store": {
                **config["vector_store"],
                "config": {
                    **config["vector_store"]["config"],
                    "connection_string": mem0_connection_string(database_url, schema),
                },
            },
        }
        self._schema = schema
        self._produced_by = f"{Role.MEMORY_EXTRACT.value}:{config['llm']['config']['model']}"
        self._model_cache_dir = model_cache_dir
        self._memory: Any = None
        self._lock = asyncio.Lock()

    async def _engine(self) -> Any:
        if self._memory is None:
            async with self._lock:
                if self._memory is None:
                    self._memory = await asyncio.to_thread(self._build)
        return self._memory

    def _build(self) -> Any:
        prepare_mem0_environment(self._config["history_db_path"], self._model_cache_dir)
        self._ensure_schema()
        from mem0 import AsyncMemory

        return AsyncMemory.from_config(self._config)

    def _ensure_schema(self) -> None:
        """mem0 creates its table but not the schema: this provider owns that storage, like the
        native provider owns its tables (not the governance migrations)."""
        import psycopg
        from psycopg import sql

        conninfo = self._config["vector_store"]["config"]["connection_string"]
        with psycopg.connect(conninfo, autocommit=True) as conn:
            conn.execute(
                sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(self._schema))
            )

    async def health(self) -> ComponentHealth:
        await self._engine()
        return ComponentHealth(self.name, "ok")

    async def add(
        self,
        messages: list[Message],
        scope: Scope,
        *,
        infer: bool = True,
        metadata: dict[str, Any] | None = None,
        idempotency_key: str | None = None,  # not honoured: no such guarantee here
    ) -> list[MemoryEvent]:
        memory = await self._engine()
        result = await memory.add(
            [{"role": m.role, "content": m.content} for m in messages],
            **_scope_kwargs(scope),
            metadata={**(metadata or {}), "namespace_id": str(scope.namespace_id)},
            infer=infer,
        )
        return [
            MemoryEvent(
                r.get("event", "ADD"),
                str(r["id"]),
                r.get("memory"),
                self._produced_by if infer else None,
            )
            for r in result.get("results", [])
        ]

    async def search(
        self, query: str, scope: Scope, *, top_k: int, filters: dict[str, Any] | None = None
    ) -> list[ProviderHit]:
        memory = await self._engine()
        users = [scope.user_id, None] if scope.user_id else [None]
        results = await asyncio.gather(
            *(memory.search(query, top_k=top_k, filters=_filters(scope, u)) for u in users)
        )
        hits = [
            ProviderHit(str(r["id"]), float(r.get("score") or 0.0), r.get("memory", ""))
            for result in results
            for r in result.get("results", [])
        ]
        return sorted(hits, key=lambda h: h.score, reverse=True)[:top_k]

    async def get(self, external_id: str) -> MemoryRecord | None:
        memory = await self._engine()
        item = await memory.get(external_id)
        return _record(item) if item else None

    async def update(self, external_id: str, content: str) -> None:
        memory = await self._engine()
        await memory.update(external_id, text=content)

    async def delete(self, external_id: str) -> None:
        memory = await self._engine()
        if await memory.get(external_id) is not None:  # already gone is fine (idempotent sync)
            await memory.delete(external_id)

    async def list(self, scope: Scope) -> list[MemoryRecord]:
        memory = await self._engine()
        result = await memory.get_all(filters=_filters(scope, scope.user_id), top_k=LIST_LIMIT)
        return [_record(r) for r in result.get("results", [])]


def _record(item: dict[str, Any]) -> MemoryRecord:
    metadata = dict(item.get("metadata") or {})
    user = item.get("user_id")
    scope = Scope(
        uuid.UUID(metadata["namespace_id"]),
        None if user in (None, SHARED_USER) else user,
        item.get("agent_id"),
        item.get("run_id"),
    )
    return MemoryRecord(str(item["id"]), item.get("memory", ""), scope, metadata)
