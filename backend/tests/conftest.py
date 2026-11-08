"""Shared fixtures.

Every DB test runs on SQLite and PostgreSQL (design §11: both governance stores).

PostgreSQL cases run against STEWARD_TEST_DATABASE_URL — locally the same `llm_steward` database the
app uses — but only inside their own schema, TEST_SCHEMA, which they drop and recreate. They never
read the app's STEWARD_DATABASE_URL (so an exported remote URL cannot pull tests along). Nothing is
created or dropped outside TEST_SCHEMA except one marker-owned table (see
test_stray_public_version_table_is_ignored). Settings rejects *_test schema names for the app.
"""

import asyncio
import functools
import os
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

import pytest
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.schema import DropSchema

from llm_steward.config import DEFAULT_DB_SCHEMA
from llm_steward.models.enums import ActorType
from llm_steward.services.governance_service import Actor
from llm_steward.storage.db import engine_scope, session_factory

if TYPE_CHECKING:
    from fastapi.testclient import TestClient
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from llm_steward.config import Settings
    from llm_steward.models.enums import ApiScope, KbStatus
    from llm_steward.services.container import Services
    from llm_steward.services.governance_service import GovernanceService

T = TypeVar("T")
ADMIN = Actor(ActorType.ADMIN, "reviewer@example.test")

TEST_SCHEMA = f"{DEFAULT_DB_SCHEMA}_test"

# Test setup, not product code: whatever the provider code does, no test run may ever send mem0
# telemetry (an external side effect). Set before anything imports mem0.
os.environ["MEM0_TELEMETRY"] = "false"


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


# --- app-level fixtures -------------------------------------------------------------------------


def migrate(url: str) -> None:
    from alembic import command

    from llm_steward.cli import alembic_config

    command.upgrade(alembic_config(url, TEST_SCHEMA), "head")


@pytest.fixture
def migrated_url(db_url: str) -> str:
    """`db_url` (SQLite or PostgreSQL, schema TEST_SCHEMA) with every migration applied."""
    migrate(db_url)
    return db_url


def test_settings(database_url: str, **overrides: object) -> "Settings":
    """Validated Settings, then pointed at TEST_SCHEMA — which `Settings` itself rejects on purpose,
    so only tests can do this, by copying past validation."""
    from llm_steward.config import Settings

    values: dict[str, object] = {
        "database_url": database_url,
        "memory_provider": "native",
        "kb_provider": "native",
        "run_worker": False,
        **overrides,
    }
    settings = Settings(_env_file=None, **values)  # type: ignore[call-arg, arg-type]
    return settings.model_copy(update={"db_schema": TEST_SCHEMA})


test_settings.__test__ = False  # type: ignore[attr-defined]  # a helper, not a test


@dataclass
class AppClient:
    """A running app plus helpers that call its services on the app's own event loop."""

    http: "TestClient"
    services: "Services"

    def call(self, fn: Callable[..., Awaitable[T]], *args: object, **kwargs: object) -> T:
        portal = self.http.portal
        assert portal is not None
        return portal.call(functools.partial(fn, *args, **kwargs))

    def new_tenant(self, slug: str, *scopes: "ApiScope") -> tuple[dict[str, str], uuid.UUID]:
        """Create a namespace and an API key; returns auth headers and the namespace id."""
        from llm_steward.services.namespace_service import DEFAULT_SCOPES

        namespace = self.call(self.services.namespaces.create_namespace, slug)
        key = self.call(self.services.namespaces.create_key, slug, scopes or DEFAULT_SCOPES)
        return {"Authorization": f"Bearer {key}"}, namespace.id

    def new_key(self, slug: str, *scopes: "ApiScope") -> dict[str, str]:
        return self.new_tenant(slug, *scopes)[0]

    def apply(self, op: object) -> Any:
        """GovernanceService.apply as an admin would (KB and candidates have no HTTP API yet)."""
        return self.call(self.services.governance.apply, op, ADMIN)

    def collection_ext_id(self, entry: Any) -> str:
        """The KB provider's id for an entry's collection, asked from the provider itself."""
        from llm_steward.providers.base import KbCollectionRef

        ref = KbCollectionRef(entry.collection_id, entry.namespace_id, "")
        return self.call(self.services.providers.kb.ensure_collection, ref)

    def drain(self) -> int:
        """Run the outbox worker until nothing is claimable (the in-process worker is off)."""
        return self.call(self.services.worker.drain)


@pytest.fixture
async def sessions(migrated_url: str) -> AsyncIterator["async_sessionmaker[AsyncSession]"]:
    async with engine_scope(migrated_url, schema=TEST_SCHEMA) as engine:
        yield session_factory(engine)


@pytest.fixture
async def namespace_id(sessions: "async_sessionmaker[AsyncSession]") -> uuid.UUID:
    from llm_steward.services.namespace_service import NamespaceService

    return (await NamespaceService(sessions).create_namespace("tenant")).id


def governance(sessions: "async_sessionmaker[AsyncSession]") -> "GovernanceService":
    from llm_steward.services.governance_service import GovernanceService

    return GovernanceService(sessions, memory_provider="native", kb_provider="native")


def kb_entry_op(
    namespace_id: uuid.UUID, collection_id: uuid.UUID, status: "KbStatus", **kw: Any
) -> Any:
    """A low-risk, human-written KB entry about refunds unless overridden."""
    from llm_steward.models.enums import AuthorType, KbOrigin, RiskLevel
    from llm_steward.services.governance_service import CreateKbEntry

    return CreateKbEntry(
        namespace_id=namespace_id,
        collection_id=collection_id,
        title=kw.get("title", "Refund time"),
        content=kw.get("content", "Refunds take 3 to 5 business days"),
        risk_level=RiskLevel.LOW,
        origin=KbOrigin.MANUAL,
        author_type=AuthorType.HUMAN,
        status=status,
    )


@contextmanager
def running_app(url: str, **overrides: Any) -> Iterator[AppClient]:
    """The app on TEST_SCHEMA with a fake embedder; `overrides` go to create_app (e.g. a fake
    memory_provider and llm for the extraction pipeline)."""
    from fastapi.testclient import TestClient

    from llm_steward.main import create_app
    from tests.fakes.embedder import FakeEmbedder

    app = create_app(test_settings(url), embedder=FakeEmbedder(), **overrides)
    with TestClient(app) as http:
        yield AppClient(http, app.state.services)


@pytest.fixture
def app_client(migrated_url: str) -> Iterator[AppClient]:
    with running_app(migrated_url) as client:
        yield client
