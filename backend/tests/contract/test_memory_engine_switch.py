"""Switching the memory engine both ways, mem0 → lite-mem → mem0 (the owner, 2026-11-12: llm-steward
must keep supporting both and switch between them). The switch is configuration plus
`steward resync`: llm-steward holds every governed memory, so the new engine is given what it
lacks — only that — and retrieval finds the memories again. Real engines: mem0 2.2.1 with the
local MiniLM model and no LLM (`infer=False` writes only), lite-mem in-process on the local
PostgreSQL with its fake embedder. Opt-in: `uv run pytest -m contract`."""

import asyncio
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select, text

from llm_steward.models import ExternalRef
from llm_steward.models.enums import SyncStatus
from llm_steward.providers.mem0_provider import Mem0Provider, build_mem0_config
from llm_steward.services.resync import resync
from llm_steward.storage.db import engine_scope
from tests.conftest import TEST_SCHEMA, AppClient, _pg_url, post_memory, running_app
from tests.contract.test_litemem_provider import _in_process
from tests.contract.test_litemem_provider import lite as lite  # a fixture
from tests.fakes.llm import FakeLLM  # no answers: direct writes call no model

pytestmark = pytest.mark.contract
MEM0_SCHEMA = "mem0_test"


def _drop_mem0_schema() -> None:
    async def drop() -> None:
        async with engine_scope(_pg_url(), schema=TEST_SCHEMA) as engine, engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{MEM0_SCHEMA}" CASCADE'))

    asyncio.run(drop())


@pytest.fixture
def mem0(tmp_path: Path) -> Iterator[Callable[[], Mem0Provider]]:
    """A fresh Mem0Provider on one collection each time it is called: the same engine, as a
    restarted service sees it."""
    _drop_mem0_schema()
    config = build_mem0_config(
        embedding_model="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        embedding_dims=384,
        llm_model="unused",
        llm_base_url="http://127.0.0.1:9/v1",  # nothing listens: an LLM call would fail loudly
        llm_api_key="unused",
        history_db_path=str(tmp_path / "history.db"),
        collection=f"c_{uuid.uuid4().hex[:8]}",
    )
    yield lambda: Mem0Provider(config, database_url=_pg_url(), schema=MEM0_SCHEMA)
    _drop_mem0_schema()


def _found(c: AppClient, h: dict[str, str], query: str) -> list[str]:
    body = {"query": query, "user_id": "u1", "scope": {"user_id": "u1"}}
    response = c.http.post("/v1/retrieve", json=body, headers=h)
    assert response.status_code == 200, response.text
    assert response.json()["degraded"] == []
    return [i["content"] for i in response.json()["items"]]


def _synced(c: AppClient, provider: str) -> set[uuid.UUID]:
    async def read() -> set[uuid.UUID]:
        async with c.sessions() as s:
            rows = await s.scalars(
                select(ExternalRef.object_id).where(
                    ExternalRef.provider == provider, ExternalRef.sync_status == SyncStatus.SYNCED
                )
            )
            return set(rows)

    return c.call(read)  # type: ignore[no-any-return]


def _resync(c: AppClient, namespace: uuid.UUID, engine: str) -> int:
    report = c.call(
        resync, c.sessions, namespace, kb_provider="native", memory_provider=engine, kb=False
    )
    c.drain()
    return report.memories  # type: ignore[no-any-return]


def test_switching_mem0_to_lite_mem_and_back(
    migrated_url: str, mem0: Callable[[], Mem0Provider], lite: Any
) -> None:
    with running_app(migrated_url, memory_provider=mem0(), llm=FakeLLM()) as c:
        h, namespace = c.new_tenant("acme")
        tea = post_memory(c, h, "likes green tea", user_id="u1")
        c.drain()
        assert _found(c, h, "green tea") == ["likes green tea"]

    switched = _in_process(lite)
    with running_app(migrated_url, memory_provider=switched, llm=FakeLLM()) as c:
        assert _found(c, h, "green tea") == []  # lite-mem holds nothing yet
        assert _resync(c, namespace, "litemem") == 1
        assert _found(c, h, "green tea") == ["likes green tea"]
        coffee = post_memory(c, h, "drinks coffee every morning", user_id="u1")  # only here
        c.drain()
        assert _synced(c, "litemem") == {uuid.UUID(tea), uuid.UUID(coffee)}

    with running_app(migrated_url, memory_provider=mem0(), llm=FakeLLM()) as c:  # and back
        assert _resync(c, namespace, "mem0") == 1  # the coffee; mem0 still holds the tea
        assert _synced(c, "mem0") == {uuid.UUID(tea), uuid.UUID(coffee)}
        # both are retrievable from mem0 again, each first for its own question
        assert _found(c, h, "green tea")[0] == "likes green tea"
        assert _found(c, h, "coffee in the morning")[0] == "drinks coffee every morning"
