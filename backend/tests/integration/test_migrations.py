import asyncio

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection

from llm_steward.cli import alembic_config
from llm_steward.models import Base
from llm_steward.models.base import include_in_migrations
from llm_steward.providers.native.providers import create_store
from llm_steward.storage.db import engine_scope
from tests.conftest import TEST_SCHEMA
from tests.fakes.embedder import FakeEmbedder

HEAD = "0011"


def _state(url: str) -> tuple[tuple[str, ...], bool]:
    """Heads recorded in the governance schema, and whether its version table exists there."""

    def read(conn: Connection) -> tuple[tuple[str, ...], bool]:
        schema = TEST_SCHEMA if conn.dialect.name == "postgresql" else None
        ctx = MigrationContext.configure(conn, opts={"version_table_schema": schema})
        return ctx.get_current_heads(), inspect(conn).has_table("alembic_version", schema=schema)

    async def run() -> tuple[tuple[str, ...], bool]:
        async with engine_scope(url, schema=TEST_SCHEMA) as engine, engine.connect() as conn:
            return await conn.run_sync(read)

    return asyncio.run(run())


def test_upgrade_and_downgrade_on_both_dialects(db_url: str) -> None:
    cfg = alembic_config(db_url, TEST_SCHEMA)

    command.upgrade(cfg, "head")
    assert _state(db_url) == ((HEAD,), True)

    command.downgrade(cfg, "base")
    assert _state(db_url) == ((), True)


def test_models_match_the_migrated_schema(db_url: str) -> None:
    """A model change without a migration (or the reverse) fails here, on both dialects."""
    command.upgrade(alembic_config(db_url, TEST_SCHEMA), "head")

    def diff(conn: Connection) -> list[object]:
        ctx = MigrationContext.configure(conn, opts={"include_name": include_in_migrations})
        return [d for d in compare_metadata(ctx, Base.metadata) if "alembic_version" not in str(d)]

    async def run() -> list[object]:
        async with engine_scope(db_url, schema=TEST_SCHEMA) as engine:
            # The native provider's own tables live next to the governance ones; they are not
            # part of the migrations and must not show up as drift.
            embedder = FakeEmbedder()
            await create_store(engine, embedder, TEST_SCHEMA).ready()
            async with engine.connect() as conn:
                return await conn.run_sync(diff)

    assert asyncio.run(run()) == []


# Marks the public.alembic_version this test plants, so it only ever drops its own table.
STRAY_MARKER = "llm-steward-test-stray"


def test_stray_public_version_table_is_ignored(pg_url: str) -> None:
    """A version table in public (e.g. from another tool) must not be mistaken for ours.

    This is the one test that writes outside TEST_SCHEMA: it needs a table on the search_path
    fallback. The table carries STRAY_MARKER; the test drops a public.alembic_version only when that
    marker is its sole content (which also clears one left by an interrupted run), and refuses to
    run if anyone else's table is there.
    """

    async def stray_rows() -> list[str] | None:
        async with engine_scope(pg_url, schema=TEST_SCHEMA) as engine, engine.connect() as conn:
            if await conn.scalar(text("SELECT to_regclass('public.alembic_version')")) is None:
                return None
            rows = await conn.execute(text("SELECT version_num FROM public.alembic_version"))
            return [r[0] for r in rows]

    async def execute(*statements: str) -> None:
        async with engine_scope(pg_url, schema=TEST_SCHEMA) as engine, engine.begin() as conn:
            for sql in statements:
                await conn.execute(text(sql))

    def drop_if_ours() -> None:
        rows = asyncio.run(stray_rows())
        if rows == [STRAY_MARKER]:
            asyncio.run(execute("DROP TABLE public.alembic_version"))
        elif rows is not None:
            pytest.fail(
                f"public.alembic_version belongs to someone else ({rows}); not touching it."
            )

    drop_if_ours()
    try:
        asyncio.run(
            execute(
                "CREATE TABLE public.alembic_version (version_num varchar(32))",
                f"INSERT INTO public.alembic_version VALUES ('{STRAY_MARKER}')",
            )
        )
        command.upgrade(alembic_config(pg_url, TEST_SCHEMA), "head")
        assert _state(pg_url) == ((HEAD,), True)
    finally:
        drop_if_ours()
