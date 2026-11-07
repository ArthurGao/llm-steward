"""Shared fixtures.

Every DB test runs on SQLite and PostgreSQL (design §11: both governance stores).

PostgreSQL cases use the same database as development (STEWARD_DATABASE_URL) but their own
schema, TEST_SCHEMA, which they drop and recreate. They never touch the app schema.
"""

import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.schema import DropSchema

from llm_steward.config import DEFAULT_DB_SCHEMA
from llm_steward.storage.db import engine_scope

SCHEMA = "steward_test"
# The fixtures DROP ... CASCADE this schema: it must never be the one holding real data.
assert SCHEMA != DEFAULT_DB_SCHEMA


class _TestEnv(BaseSettings):
    """Reads STEWARD_DATABASE_URL from the environment or backend/.env."""

    model_config = SettingsConfigDict(env_prefix="STEWARD_", env_file=".env", extra="ignore")
    database_url: str | None = None


def _pg_test_url() -> str:
    url = _TestEnv().database_url
    if not url or make_url(url).get_backend_name() != "postgresql":
        pytest.fail(
            "PostgreSQL cases need STEWARD_DATABASE_URL (env or backend/.env) to point at "
            f"PostgreSQL; got {url!r}. Tests use schema {SCHEMA!r} only."
        )
    return url


def _drop_schema(url: str) -> None:
    async def run() -> None:
        async with engine_scope(url, schema=SCHEMA) as engine, engine.begin() as conn:
            await conn.execute(DropSchema(SCHEMA, cascade=True, if_exists=True))
            # Left behind if a migration ever runs while the schema is missing (search_path falls
            # through to public); it must not leak into the next test.
            await conn.execute(text("DROP TABLE IF EXISTS public.alembic_version"))

    asyncio.run(run())


@pytest.fixture(params=["sqlite", "pg"])
def db_url(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[str]:
    if request.param == "sqlite":
        yield f"sqlite+aiosqlite:///{tmp_path / 'steward.db'}"
        return
    url = _pg_test_url()
    _drop_schema(url)
    yield url
    _drop_schema(url)


@pytest.fixture
def pg_url() -> Iterator[str]:
    """PostgreSQL only, for behaviour that has no SQLite counterpart (schemas)."""
    url = _pg_test_url()
    _drop_schema(url)
    yield url
    _drop_schema(url)


@pytest.fixture
def unreachable_db_url() -> str:
    """Nothing listens on port 1, so connecting fails immediately."""
    return "postgresql+asyncpg://nobody:x@127.0.0.1:1/none"
