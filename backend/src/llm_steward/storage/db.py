"""Database engine for the governance store: SQLite (native profile) or PostgreSQL.

All dialect-specific setup lives here so the rest of the code stays portable.
"""

import functools
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager, contextmanager
from contextvars import ContextVar
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
from sqlalchemy.orm import Session
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


# Tenant isolation, second line (design §4 "租户隔离", P12): on PostgreSQL every governance
# transaction starts by telling the row-level security policies whose rows it may touch. The
# application's own `namespace_id` filters stay (rule 7); this catches the one that is missing.
class _Every:
    """`unscoped()`'s scope: every namespace."""


EVERY = _Every()
# The current transaction scope: a namespace, EVERY (`unscoped`), or None (nothing visible).
_SCOPE: ContextVar[uuid.UUID | _Every | None] = ContextVar("steward_scope", default=None)


@contextmanager
def namespace_scope(namespace_id: uuid.UUID) -> Iterator[None]:
    """Transactions inside see and write only `namespace_id`'s rows (also inside `unscoped`):
    a request (its principal's namespace), a job, one namespace of a sweep."""
    token = _SCOPE.set(namespace_id)
    try:
        yield
    finally:
        _SCOPE.reset(token)


@contextmanager
def unscoped() -> Iterator[None]:
    """Transactions inside see every namespace: only the paths that are cross-namespace by
    nature (the job queue, a sweep's scan, the CLI's maintenance commands; design §4 lists
    them). Anything else runs scoped, and with no scope at all sees nothing (fail-closed)."""
    token = _SCOPE.set(EVERY)
    try:
        yield
    finally:
        _SCOPE.reset(token)


def run_unscoped[**P, R](fn: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
    """`fn` (a coroutine function) always runs `unscoped()`: a sweep over every namespace."""

    @functools.wraps(fn)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        with unscoped():
            return await fn(*args, **kwargs)

    return wrapper


async def per_namespace[T](
    items: Sequence[tuple[uuid.UUID, T]],
    apply: Callable[[T], Awaitable[object]],
    *,
    skip: tuple[type[Exception], ...] = (),
) -> int:
    """A sweep's second half: `apply` to each item of an (unscoped) scan, each in its own
    namespace's scope; an item whose `apply` raises one of `skip` (it changed between the scan
    and now) is passed over, not the whole sweep. Returns how many were applied."""
    done = 0
    for namespace_id, item in items:
        try:
            with namespace_scope(namespace_id):
                await apply(item)
        except skip:
            continue
        done += 1
    return done


def enter_namespace(namespace_id: uuid.UUID) -> None:
    """`namespace_scope` for the rest of the current task: a request, once its principal (or
    the Admin's namespace) is known. Each request runs in its own task, so it never leaks; a
    yield dependency would reset it, but its teardown timing does not fit streamed responses."""
    _SCOPE.set(namespace_id)


Sessions = Callable[[], AbstractAsyncContextManager[AsyncSession]]


def unscoped_sessions(sessions: Sessions) -> Sessions:
    """`sessions` whose every session runs `unscoped()`: for a component that is cross-namespace
    by nature (the job queue)."""

    @asynccontextmanager
    async def session() -> AsyncIterator[AsyncSession]:
        with unscoped():
            async with sessions() as s:
                yield s

    return session


class GovernanceSession(Session):
    """The governance store's session: each transaction sets the tenant GUCs (`_tenant`)."""


@event.listens_for(GovernanceSession, "after_begin")
def _tenant(session: Session, transaction: Any, connection: Any) -> None:
    if connection.dialect.name != "postgresql":
        return  # SQLite has no row-level security: the application's filters only
    scope = _SCOPE.get()
    connection.execute(
        text(
            "SELECT set_config('app.namespace_id', :ns, true),"
            " set_config('app.rls_bypass', :bypass, true)"
        ),
        {
            "ns": str(scope) if isinstance(scope, uuid.UUID) else "",
            "bypass": "on" if scope is EVERY else "off",
        },
    )


def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Objects stay readable after commit, so services can return what they just wrote. Each
    transaction is scoped by `namespace_scope` / `unscoped` (P12)."""
    return async_sessionmaker(engine, expire_on_commit=False, sync_session_class=GovernanceSession)


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


async def rls_bypassed(engine: AsyncEngine) -> str | None:
    """Why row-level security would not hold this engine's role, or None when it does: on
    SQLite there is none, a superuser and a BYPASSRLS role skip every policy (P12)."""
    if engine.dialect.name != "postgresql":
        return "the database is SQLite, which has no row-level security"
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
            )
        ).one()
    if row.rolsuper:
        return "the database role is a superuser, which skips every policy"
    if row.rolbypassrls:
        return "the database role has BYPASSRLS"
    return None


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
