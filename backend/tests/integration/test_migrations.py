import asyncio

from alembic import command
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection

from llm_steward.cli import alembic_config
from llm_steward.storage.db import engine_scope
from tests.conftest import SCHEMA


def _state(url: str) -> tuple[tuple[str, ...], bool]:
    """Heads recorded in the governance schema, and whether its version table exists there."""

    def read(conn: Connection) -> tuple[tuple[str, ...], bool]:
        schema = SCHEMA if conn.dialect.name == "postgresql" else None
        ctx = MigrationContext.configure(conn, opts={"version_table_schema": schema})
        return ctx.get_current_heads(), inspect(conn).has_table("alembic_version", schema=schema)

    async def run() -> tuple[tuple[str, ...], bool]:
        async with engine_scope(url, schema=SCHEMA) as engine, engine.connect() as conn:
            return await conn.run_sync(read)

    return asyncio.run(run())


def test_upgrade_and_downgrade_on_both_dialects(db_url: str) -> None:
    cfg = alembic_config(db_url, SCHEMA)

    command.upgrade(cfg, "head")
    assert _state(db_url) == (("0001",), True)

    command.downgrade(cfg, "base")
    assert _state(db_url) == ((), True)


def test_stray_public_version_table_is_ignored(pg_url: str) -> None:
    """A version table in public (e.g. from another tool) must not be mistaken for ours."""

    async def plant() -> None:
        async with engine_scope(pg_url, schema=SCHEMA) as engine, engine.begin() as conn:
            await conn.execute(
                text("CREATE TABLE public.alembic_version (version_num varchar(32))")
            )
            await conn.execute(text("INSERT INTO public.alembic_version VALUES ('foreign')"))

    asyncio.run(plant())
    command.upgrade(alembic_config(pg_url, SCHEMA), "head")
    assert _state(pg_url) == (("0001",), True)
