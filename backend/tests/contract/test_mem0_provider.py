"""Provider contract against the real mem0 2.2.1 (opt-in: `uv run pytest -m contract`).

Runs on the local PostgreSQL with the real MiniLM model, in its own schema `mem0_test` (created and
dropped here; the app's `mem0` schema is never touched). `infer=False` only: no LLM is called —
the LLM endpoint below is deliberately unreachable.
"""

import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from sqlalchemy import text

from llm_steward.providers.base import Message, Scope
from llm_steward.providers.mem0_provider import Mem0Provider, build_mem0_config
from llm_steward.storage.db import engine_scope
from tests.conftest import TEST_SCHEMA, _pg_url

pytestmark = pytest.mark.contract

MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
SCHEMA = "mem0_test"
NS = uuid.UUID("00000000-0000-7000-8000-00000000000a")
OTHER_NS = uuid.UUID("00000000-0000-7000-8000-00000000000b")


async def _schema(url: str, sql: str) -> None:
    async with engine_scope(url, schema=TEST_SCHEMA) as engine, engine.begin() as conn:
        await conn.execute(text(sql))


@pytest.fixture
def pg() -> Iterator[str]:
    yield _pg_url()


@pytest.fixture
async def mem0(pg: str, tmp_path: Path) -> AsyncIterator[Mem0Provider]:
    await _schema(pg, f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE')  # the provider recreates it
    config = build_mem0_config(
        embedding_model=MODEL,
        embedding_dims=384,
        llm_model="unused",
        llm_base_url="http://127.0.0.1:9/v1",  # nothing listens: an LLM call would fail loudly
        llm_api_key="unused",
        history_db_path=str(tmp_path / "history.db"),
        collection=f"c_{uuid.uuid4().hex[:8]}",
    )
    yield Mem0Provider(config, database_url=pg, schema=SCHEMA)
    await _schema(pg, f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE')


async def _add(p: Mem0Provider, content: str, scope: Scope, steward_id: str) -> str:
    [event] = await p.add(
        [Message("user", content)], scope, infer=False, metadata={"steward_id": steward_id}
    )
    assert event.op == "ADD"
    return event.external_id


async def test_lifecycle(mem0: Mem0Provider) -> None:
    assert (await mem0.health()).status == "ok"
    import mem0.memory.setup as mem0_setup
    import mem0.memory.telemetry as mem0_telemetry

    assert mem0_telemetry.MEM0_TELEMETRY is False
    assert not mem0_setup.mem0_dir.startswith(str(Path.home() / ".mem0"))
    scope = Scope(NS, user_id="u1")
    eid = await _add(mem0, "The user lives in Beijing", scope, "s-1")

    hits = await mem0.search("Which city does the user live in?", scope, top_k=5)
    assert [h.external_id for h in hits] == [eid]

    await mem0.update(eid, "The user moved to Shanghai")
    record = await mem0.get(eid)
    assert record is not None and record.content == "The user moved to Shanghai"
    assert record.scope == scope and record.metadata["steward_id"] == "s-1"

    await mem0.delete(eid)
    await mem0.delete(eid)  # idempotent
    assert await mem0.get(eid) is None
    assert await mem0.search("Shanghai", scope, top_k=5) == []


async def test_scope_namespace_and_shared_memories(mem0: Mem0Provider) -> None:
    mine = await _add(mem0, "likes green tea", Scope(NS, "alice"), "a")
    await _add(mem0, "likes green tea", Scope(NS, "bob"), "b")
    shared = await _add(mem0, "the office serves green tea", Scope(NS), "s")
    await _add(mem0, "likes green tea", Scope(OTHER_NS, "alice"), "x")

    found = {h.external_id for h in await mem0.search("green tea", Scope(NS, "alice"), top_k=10)}
    assert found == {mine, shared}  # own + shared; never bob's, never another namespace's
    anonymous = {h.external_id for h in await mem0.search("green tea", Scope(NS), top_k=10)}
    assert anonymous == {shared}
    listed = {r.external_id for r in await mem0.list(Scope(NS, "bob"))}
    assert len(listed) == 1 and mine not in listed
    [shared_record] = await mem0.list(Scope(NS))
    assert shared_record.scope.user_id is None  # the sentinel never leaks out
