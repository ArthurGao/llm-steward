import pytest
from sqlalchemy import text

from llm_steward.storage.db import create_engine, engine_scope, ping, prepare_schema
from tests.conftest import SCHEMA


async def test_engine_dialect_setup(db_url: str) -> None:
    async with engine_scope(db_url, schema=SCHEMA) as engine:
        await ping(engine)
        async with engine.connect() as conn:
            if engine.dialect.name == "postgresql":
                search_path = (await conn.execute(text("SHOW search_path"))).scalar_one()
                assert search_path == f"{SCHEMA}, public"
            else:
                assert (await conn.execute(text("PRAGMA foreign_keys"))).scalar_one() == 1
                assert (await conn.execute(text("PRAGMA journal_mode"))).scalar_one() == "wal"


async def test_prepare_schema_creates_it_once(db_url: str) -> None:
    async with engine_scope(db_url, schema=SCHEMA) as engine:
        await prepare_schema(engine, SCHEMA)
        await prepare_schema(engine, SCHEMA)  # idempotent
        async with engine.connect() as conn:
            if engine.dialect.name == "postgresql":
                current = (await conn.execute(text("SELECT current_schema()"))).scalar_one()
                assert current == SCHEMA
            else:
                assert (await conn.execute(text("SELECT 1"))).scalar_one() == 1


async def test_ping_raises_when_database_unreachable(unreachable_db_url: str) -> None:
    async with engine_scope(unreachable_db_url, schema=SCHEMA) as engine:
        with pytest.raises(OSError):
            await ping(engine)


def test_rejects_unsupported_backend() -> None:
    with pytest.raises(ValueError, match="mysql"):
        create_engine("mysql+aiomysql://u:p@localhost/db", schema=SCHEMA)
