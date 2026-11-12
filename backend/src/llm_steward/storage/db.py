"""Database engine for the governance store: SQLite (native profile) or PostgreSQL.

All dialect-specific setup lives here so the rest of the code stays portable.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import Boolean, event, func, text, type_coerce
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.schema import CreateSchema
from sqlalchemy.sql.functions import FunctionElement

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


def libpq_url(database_url: str | URL) -> str | None:
    """The governance URL as a plain libpq URL, for engines that open their own psycopg
    connections to the same database (mem0, lite-mem); None when it is not PostgreSQL."""
    url = make_url(database_url)
    if url.get_backend_name() != "postgresql":
        return None
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Objects stay readable after commit, so services can return what they just wrote."""
    return async_sessionmaker(engine, expire_on_commit=False)


async def prepare_schema(engine: AsyncEngine, schema: str) -> None:
    """Create the governance schema on PostgreSQL; SQLite has no schemas."""
    if engine.dialect.name != "postgresql":
        return
    async with engine.begin() as conn:
        await conn.execute(CreateSchema(schema, if_not_exists=True))


class json_array_contains(FunctionElement[bool]):
    """`json_array_contains(column, value)`: `value` is an element of the JSON array in `column`.

    Compiled per dialect, so callers never branch: JSONB `@>` on PostgreSQL (the column's base type
    is JSON, whose own `contains` would compile to LIKE), `json_each` on SQLite."""

    type = Boolean()
    name = "json_array_contains"
    inherit_cache = True


@compiles(json_array_contains, "postgresql")
def _json_array_contains_pg(element: json_array_contains, compiler: Any, **kw: Any) -> str:
    column, value = element.clauses.clauses
    expr = type_coerce(column, JSONB).op("@>")(func.jsonb_build_array(value))
    return str(compiler.process(expr, **kw))


@compiles(json_array_contains, "sqlite")
def _json_array_contains_sqlite(element: json_array_contains, compiler: Any, **kw: Any) -> str:
    column, value = element.clauses.clauses
    return (
        f"EXISTS (SELECT 1 FROM json_each({compiler.process(column, **kw)})"
        f" WHERE value = {compiler.process(value, **kw)})"
    )


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
    _load_sqlite_vec(dbapi_connection)


def _load_sqlite_vec(dbapi_connection: Any) -> None:
    """Load sqlite-vec (the native provider's vector index) when the `native` extra is installed.

    `dbapi_connection` is SQLAlchemy's aiosqlite adapter; `run_async` reaches the real connection.
    Extension loading is switched off again right after, so SQL cannot load anything else.
    """
    try:
        import sqlite_vec
    except ImportError:
        return
    path = sqlite_vec.loadable_path()
    dbapi_connection.run_async(lambda c: c.enable_load_extension(True))
    try:
        dbapi_connection.run_async(lambda c: c.load_extension(path))
    finally:
        dbapi_connection.run_async(lambda c: c.enable_load_extension(False))
