"""Shared fixtures.

Every DB test runs on SQLite and PostgreSQL (design §11: both governance stores).

PostgreSQL cases run against STEWARD_TEST_DATABASE_URL — locally the same `llm_steward` database the
app uses — but only inside their own schema, TEST_SCHEMA, which they drop and recreate. They never
read the app's STEWARD_DATABASE_URL (so an exported remote URL cannot pull tests along). Nothing is
created or dropped outside TEST_SCHEMA except one marker-owned table (see
test_stray_public_version_table_is_ignored). Settings rejects *_test schema names for the app.
"""

import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.schema import DropSchema

from llm_steward.config import DEFAULT_DB_SCHEMA
from llm_steward.storage.db import engine_scope

TEST_SCHEMA = f"{DEFAULT_DB_SCHEMA}_test"


class _TestTarget(BaseSettings):
    """Where PG tests may run, and which schema the app is configured to use (env or .env)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str | None = Field(default=None, validation_alias="STEWARD_TEST_DATABASE_URL")
    app_schema: str = Field(default=DEFAULT_DB_SCHEMA, validation_alias="STEWARD_DB_SCHEMA")


def _pg_url() -> str:
    target = _TestTarget()
    url = target.database_url
    if not url or make_url(url).get_backend_name() != "postgresql":
        shown = make_url(url).render_as_string(hide_password=True) if url else None
        pytest.fail(
            "PostgreSQL cases need STEWARD_TEST_DATABASE_URL (env or backend/.env) pointing at "
            f"PostgreSQL; got {shown!r}."
        )
    if target.app_schema == TEST_SCHEMA:
        # The fixtures DROP ... CASCADE TEST_SCHEMA: it must never be the schema holding app data.
        pytest.fail(
            f"STEWARD_DB_SCHEMA is {TEST_SCHEMA!r}, the schema tests drop. Refusing to run."
        )
    return url


def _drop_test_schema(url: str) -> None:
    async def run() -> None:
        async with engine_scope(url, schema=TEST_SCHEMA) as engine, engine.begin() as conn:
            await conn.execute(DropSchema(TEST_SCHEMA, cascade=True, if_exists=True))

    asyncio.run(run())


def _pg_test_schema() -> Iterator[str]:
    url = _pg_url()
    _drop_test_schema(url)
    yield url
    _drop_test_schema(url)


@pytest.fixture(params=["sqlite", "pg"])
def db_url(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[str]:
    if request.param == "sqlite":
        yield f"sqlite+aiosqlite:///{tmp_path / 'steward.db'}"
    else:
        yield from _pg_test_schema()


@pytest.fixture
def pg_url() -> Iterator[str]:
    """PostgreSQL only, for behaviour that has no SQLite counterpart (schemas)."""
    yield from _pg_test_schema()


@pytest.fixture
def unreachable_db_url() -> str:
    """Nothing listens on port 1, so connecting fails immediately."""
    return "postgresql+asyncpg://nobody:x@127.0.0.1:1/none"
