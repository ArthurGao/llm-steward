"""Database engine for the governance store: SQLite (native profile) or PostgreSQL.

All dialect-specific setup lives here so the rest of the code stays portable.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.schema import CreateSchema

# Fail fast when the database is unreachable instead of hanging a health check.
CONNECT_TIMEOUT_SECONDS = 5


def create_engine(database_url: str | URL, *, schema: str) -> AsyncEngine:
    url = make_url(database_url)
    backend = url.get_backend_name()
    if backend == "postgresql":
        return create_async_engine(
            url,
            pool_pre_ping=True,
            connect_args={
                "timeout": CONNECT_TIMEOUT_SECONDS,
                # Governance tables in `schema`; extensions such as pgvector stay in public.
                "server_settings": {"search_path": f"{schema}, public"},
            },
        )
    if backend == "sqlite":
        _ensure_sqlite_dir(url)
        engine = create_async_engine(url, connect_args={"timeout": CONNECT_TIMEOUT_SECONDS})
        event.listen(engine.sync_engine, "connect", _sqlite_on_connect)
        return engine
    raise ValueError(f"Unsupported database backend: {backend!r} (use postgresql or sqlite)")


@asynccontextmanager
async def engine_scope(database_url: str | URL, *, schema: str) -> AsyncIterator[AsyncEngine]:
    """A short-lived engine that is always disposed (CLI, migrations, tests)."""
    engine = create_engine(database_url, schema=schema)
    try:
        yield engine
    finally:
        await engine.dispose()


async def prepare_schema(engine: AsyncEngine, schema: str) -> None:
    """Create the governance schema on PostgreSQL; SQLite has no schemas."""
    if engine.dialect.name != "postgresql":
        return
    async with engine.begin() as conn:
        await conn.execute(CreateSchema(schema, if_not_exists=True))


async def ping(engine: AsyncEngine) -> None:
    """Raise if the database cannot answer a trivial query."""
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


def _ensure_sqlite_dir(url: URL) -> None:
    if url.database and url.database != ":memory:":
        Path(url.database).parent.mkdir(parents=True, exist_ok=True)


def _sqlite_on_connect(dbapi_connection: Any, _record: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()
