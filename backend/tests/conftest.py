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
REVIEWER = ADMIN  # an admin acts as a reviewer (P5: an admin's reviewer role)

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


@pytest.fixture
def cli_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """A migrated SQLite file the `steward` CLI uses (via STEWARD_DATABASE_URL), run away from
    backend/.env so no real database can be picked up. Returns its URL."""
    url = f"sqlite+aiosqlite:///{tmp_path / 'cli.db'}"
    migrate(url)
    monkeypatch.setenv("STEWARD_DATABASE_URL", url)
    monkeypatch.chdir(tmp_path)
    return url


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

    @property
    def sessions(self) -> "async_sessionmaker[AsyncSession]":
        """Sessions on the app's own engine (use them inside `call`)."""
        from llm_steward.storage.db import session_factory

        return session_factory(self.services.engine)

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
        """GovernanceService.apply as an admin (for setups the HTTP API does not offer)."""
        return self.call(self.services.governance.apply, op, ADMIN)

    def collection_ext_id(self, entry: Any) -> str:
        """The KB provider's id for an entry's collection, asked from the provider itself."""
        from llm_steward.providers.base import KbCollectionRef

        ref = KbCollectionRef(entry.collection_id, entry.namespace_id, "")
        return self.call(self.services.providers.kb.ensure_collection, ref)

    def drain(self) -> int:
        """Run the outbox worker until nothing is claimable (the in-process worker is off)."""
        return self.call(self.services.worker.drain)

    def get(self, model: Any, object_id: Any) -> Any:
        """One row by primary key (a str id is parsed), read in its own session."""

        key = uuid.UUID(object_id) if isinstance(object_id, str) else object_id

        async def read() -> Any:
            async with self.sessions() as s:
                return await s.get(model, key)

        return self.call(read)

    def retrieve(self, headers: dict[str, str], **body: Any) -> dict[str, Any]:
        """POST /v1/retrieve, asserting 200; the response body."""
        r = self.http.post("/v1/retrieve", json=body, headers=headers)
        assert r.status_code == 200, r.text
        return dict(r.json())

    def set_config(
        self, namespace_id: uuid.UUID, config: dict[str, Any], *, validate: bool = True
    ) -> None:
        """A namespace's config (design §8), validated as `steward ns apply` does; `validate=False`
        stores it raw, like a row written before validation existed."""
        from llm_steward.models import Namespace
        from llm_steward.namespace_config import NamespaceConfig

        if validate:
            config = NamespaceConfig.parse(config).dump()

        async def write() -> None:
            async with self.sessions() as s, s.begin():
                namespace = await s.get(Namespace, namespace_id)
                assert namespace is not None
                namespace.config = config

        self.call(write)

    def make_jobs_due(self) -> None:
        """Make every waiting job claimable now (e.g. parse-status polls scheduled ahead)."""
        from sqlalchemy import update

        from llm_steward.models import Job
        from llm_steward.models.base import utcnow
        from llm_steward.models.enums import JobStatus

        async def due() -> None:
            async with self.sessions() as s, s.begin():
                pending = Job.status == JobStatus.PENDING
                await s.execute(update(Job).where(pending).values(run_after=utcnow()))

        self.call(due)


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
def running_app(
    url: str, *, settings: dict[str, Any] | None = None, **overrides: Any
) -> Iterator[AppClient]:
    """The app on TEST_SCHEMA with a fake embedder; `settings` override Settings fields, and
    `overrides` go to create_app (e.g. a fake memory_provider and llm for the pipeline)."""
    from fastapi.testclient import TestClient

    from llm_steward.main import create_app
    from tests.fakes.embedder import FakeEmbedder

    overrides.setdefault("embedder", FakeEmbedder())  # a real one for the opt-in eval
    app = create_app(test_settings(url, **(settings or {})), **overrides)
    with TestClient(app) as http:
        yield AppClient(http, app.state.services)


@pytest.fixture
def app_client(migrated_url: str) -> Iterator[AppClient]:
    with running_app(migrated_url) as client:
        yield client


def post_memory(c: AppClient, headers: dict[str, str], content: str, **body: Any) -> str:
    """POST /v1/memories; the new memory's id."""
    r = c.http.post("/v1/memories", json={"content": content, **body}, headers=headers)
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


def fix_order(monkeypatch: pytest.MonkeyPatch, provider: Any, external_ids: list[str]) -> None:
    """The provider's search returns exactly these, in this order (rank 1 first): tests of what
    governance does with ranks, not of the engines' ranking."""
    from llm_steward.providers.base import ProviderHit

    async def search(query: str, *args: Any, **kwargs: Any) -> list[ProviderHit]:
        return [ProviderHit(e, 1.0 - i / 100, "") for i, e in enumerate(external_ids)]

    monkeypatch.setattr(provider, "search", search)


class Tenant:
    """One namespace in a running app, with a host-app key (read/write) and a reviewer key
    (review scope)."""

    def __init__(self, c: AppClient, slug: str) -> None:
        from llm_steward.models.enums import ApiScope

        self.c = c
        self.host, self.ns = c.new_tenant(slug, ApiScope.READ, ApiScope.WRITE)
        key = c.call(
            c.services.namespaces.create_key, slug, (ApiScope.READ, ApiScope.WRITE, ApiScope.REVIEW)
        )
        self.reviewer = {"Authorization": f"Bearer {key}"}

    def post(self, path: str, body: Any = None, *, as_reviewer: bool = False) -> Any:
        headers = self.reviewer if as_reviewer else self.host
        return self.c.http.post(path, json=body or {}, headers=headers)

    def collection(self, **body: Any) -> str:
        body = {"name": f"c-{uuid.uuid4().hex[:6]}", **body}
        r = self.post("/v1/kb/collections", body, as_reviewer=True)
        assert r.status_code == 201, r.text
        return str(r.json()["id"])

    def upload(
        self,
        collection: str,
        data: bytes,
        *,
        filename: str = "policy.md",
        as_reviewer: bool = False,
        **form: str,
    ) -> Any:
        """POST /v1/kb/documents (multipart); the response."""
        return self.c.http.post(
            "/v1/kb/documents",
            data={"collection_id": collection, **form},
            files={"file": (filename, data, "text/markdown")},
            headers=self.reviewer if as_reviewer else self.host,
        )

    def submit(self, collection: str, content: str, **body: Any) -> Any:
        payload = {"collection_id": collection, "title": "Refund time", "content": content, **body}
        return self.post("/v1/kb/entries", payload)

    def retrieve(self, query: str = "refunds") -> list[str]:
        body = {"query": query, "sources": ["kb"]}
        r = self.c.http.post("/v1/retrieve", json=body, headers=self.host)
        assert r.status_code == 200, r.text
        return [i["id"] for i in r.json()["items"]]

    def in_provider(self, entry: dict[str, Any]) -> bool:
        """Whether the KB provider itself returns the entry (no governance filter)."""
        from llm_steward.providers.base import KbCollectionRef

        kb = self.c.services.providers.kb
        ref = KbCollectionRef(uuid.UUID(entry["collection_id"]), self.ns, "")
        hits = self.c.call(kb.search, "refunds", [self.c.call(kb.ensure_collection, ref)], top_k=5)
        return entry["id"] in {h.external_id for h in hits}


@pytest.fixture
def t(app_client: AppClient) -> Tenant:
    return Tenant(app_client, "acme")
