"""Row-level security, the second line of tenant isolation (design §4, P12). PostgreSQL only.

The tests connect as the role every PostgreSQL test uses: the owner of the tables, held to the
policies by FORCE. A superuser or BYPASSRLS role would pass these tests without any policy
working, so the first test refuses to run under one: in CI too (a skip here is a failure)."""

import asyncio
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from llm_steward.models import Memory, Namespace
from llm_steward.models.base import utcnow
from llm_steward.models.enums import MemoryOrigin, MemoryStatus
from llm_steward.storage.db import engine_scope, namespace_scope, session_factory, unscoped
from tests.conftest import TEST_SCHEMA, migrate


@pytest.fixture
def pg(pg_url: str) -> Iterator[str]:
    migrate(pg_url)
    yield pg_url


def _memory(ns: uuid.UUID, content: str) -> Memory:
    return Memory(
        namespace_id=ns,
        content=content,
        content_hash=content,
        memory_type="semantic",
        origin=MemoryOrigin.DIRECT,
        valid_from=utcnow(),
        status=MemoryStatus.ACTIVE,
    )


async def _seed(url: str) -> tuple[uuid.UUID, uuid.UUID]:
    async with engine_scope(url, schema=TEST_SCHEMA) as engine:
        sessions = session_factory(engine)
        with unscoped():
            async with sessions() as s, s.begin():
                a, b = Namespace(slug="rls-a", name="A"), Namespace(slug="rls-b", name="B")
                s.add_all([a, b])
                await s.flush()
                s.add_all([_memory(a.id, "a's fact"), _memory(b.id, "b's fact")])
            return a.id, b.id


async def _contents(url: str, scope: object) -> list[str]:
    async with engine_scope(url, schema=TEST_SCHEMA) as engine:
        sessions = session_factory(engine)
        with scope:  # type: ignore[attr-defined]
            async with sessions() as s:
                return sorted(await s.scalars(select(Memory.content)))


def test_the_policies_hold_the_test_role(pg: str) -> None:
    async def role() -> tuple[bool, bool, int]:
        async with engine_scope(pg, schema=TEST_SCHEMA) as engine, engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT rolsuper, rolbypassrls,"
                        " (SELECT count(*) FROM pg_policies WHERE schemaname = :schema)"
                        " FROM pg_roles WHERE rolname = current_user"
                    ),
                    {"schema": TEST_SCHEMA},
                )
            ).one()
            return bool(row[0]), bool(row[1]), int(row[2])

    superuser, bypass, policies = asyncio.run(role())
    assert not superuser and not bypass, (
        "the PostgreSQL test role bypasses row-level security: these tests would prove nothing"
    )
    assert policies == 14  # one per table holding a namespace's rows (migration 0017)


def test_a_transaction_sees_only_its_namespace(pg: str) -> None:
    a, b = asyncio.run(_seed(pg))
    assert asyncio.run(_contents(pg, namespace_scope(a))) == ["a's fact"]
    assert asyncio.run(_contents(pg, namespace_scope(b))) == ["b's fact"]
    assert asyncio.run(_contents(pg, unscoped())) == ["a's fact", "b's fact"]


def test_with_no_scope_nothing_is_visible(pg: str) -> None:
    asyncio.run(_seed(pg))

    async def count() -> int:
        async with engine_scope(pg, schema=TEST_SCHEMA) as engine:
            async with session_factory(engine)() as s:
                return int(await s.scalar(select(func.count()).select_from(Memory)) or 0)

    assert asyncio.run(count()) == 0  # fail-closed: a forgotten scope reads nothing


def test_a_write_into_another_namespace_is_refused(pg: str) -> None:
    a, b = asyncio.run(_seed(pg))

    async def write() -> None:
        async with engine_scope(pg, schema=TEST_SCHEMA) as engine:
            with namespace_scope(a):
                async with session_factory(engine)() as s, s.begin():
                    s.add(_memory(b, "planted"))

    with pytest.raises(DBAPIError, match="row-level security"):
        asyncio.run(write())


def test_a_scope_inside_unscoped_narrows_again(pg: str) -> None:
    a, _ = asyncio.run(_seed(pg))

    async def nested() -> list[str]:
        async with engine_scope(pg, schema=TEST_SCHEMA) as engine:
            sessions = session_factory(engine)
            with unscoped(), namespace_scope(a):  # a job run by an unscoped worker loop
                async with sessions() as s:
                    return sorted(await s.scalars(select(Memory.content)))

    assert asyncio.run(nested()) == ["a's fact"]


def test_require_rls_refuses_where_the_policies_would_not_hold(pg: str, tmp_path: Any) -> None:
    from llm_steward.config import Settings
    from llm_steward.services.container import runtime
    from llm_steward.storage.db import rls_bypassed
    from tests.fakes.memory_provider import FakeMemoryProvider

    async def why(url: str) -> str | None:
        async with engine_scope(url, schema=TEST_SCHEMA) as engine:
            return await rls_bypassed(engine)

    assert asyncio.run(why(pg)) is None  # this role is held to the policies

    async def start() -> None:
        settings = Settings(  # type: ignore[call-arg]
            _env_file=None,
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'x.db'}",
            require_rls=True,
            memory_provider="native",
            kb_provider="native",
        )
        async with runtime(settings, memory_provider=FakeMemoryProvider()):
            pass

    with pytest.raises(RuntimeError, match="STEWARD_REQUIRE_RLS: the database is SQLite"):
        asyncio.run(start())
