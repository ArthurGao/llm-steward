"""The memory provider contract for lite-mem (S9): mem0's lifecycle and scope assertions, plus what
only an engine that resolves conflicts does — run on a real lite-mem (its library, on the local
PostgreSQL in its own schema `litemem_test`, created and dropped here) with lite-mem's fake
embedder and fake LLM (no Ollama), in-process and behind its real HTTP app (API keys on, reached
through ASGI). Marked `contract`: `uv run pytest -m contract`."""

import uuid
from collections.abc import Iterator
from typing import Any

import httpx2
import pytest
from sqlalchemy.engine import make_url

from llm_steward.errors import ProviderUnavailable
from llm_steward.providers.base import Message, Scope
from llm_steward.providers.litemem import HttpTransport, InProcessTransport, LiteMemProvider
from tests.conftest import _pg_url

pytestmark = pytest.mark.contract
SCHEMA = "litemem_test"
NS, OTHER_NS = uuid.uuid4(), uuid.uuid4()


@pytest.fixture
def lite() -> Iterator[Any]:
    """lite-mem's components on the local PostgreSQL: a fake embedder, a fake extractor (each user
    message is one fact) and a fake resolver ("搬", moved: the first related memory is replaced)."""
    import psycopg
    from lite_mem.db import migrate
    from lite_mem.extract import LlmExtractor
    from lite_mem.factory import Components
    from lite_mem.resolve import LlmResolver
    from lite_mem.stores.pg import PgStore
    from lite_mem.testing import FakeEmbedder, FakeLLM, update_when, user_lines

    url = make_url(_pg_url()).set(drivername="postgresql").render_as_string(hide_password=False)
    drop = f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE'
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(drop)
        migrate(conn, SCHEMA)
    llm = FakeLLM(extract=user_lines, resolve=update_when("搬"))
    store = PgStore(url, SCHEMA)
    yield Components(store, FakeEmbedder(dim=16), LlmExtractor(llm), LlmResolver(llm))
    store.close()
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(drop)


def _in_process(components: Any) -> LiteMemProvider:
    from lite_mem.factory import Engines

    return LiteMemProvider(InProcessTransport(components, Engines(components), concurrency=4))


def _app(components: Any, namespaces: list[str]) -> tuple[httpx2.AsyncClient, str]:
    """lite-mem's real HTTP app with one key, reached in-process through ASGI."""
    from lite_mem.config import ServerConfig, Settings
    from lite_mem.server.app import create_app
    from lite_mem.server.auth import new_key

    token, key = new_key("steward", namespaces)
    settings = Settings(namespace="app", server=ServerConfig(api_keys=[key]))
    app = create_app(settings, components)
    return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app)), token


def _over_http(http: httpx2.AsyncClient, token: str | None) -> LiteMemProvider:
    return LiteMemProvider(HttpTransport(http, "http://lite-mem", token, timeout_s=10))


async def _push(p: LiteMemProvider, content: str, scope: Scope, steward_id: str) -> str:
    [event] = await p.add(  # as the Syncer pushes a governed memory
        [Message("user", content)], scope, infer=False, metadata={"steward_id": steward_id},
        idempotency_key=steward_id,
    )  # fmt: skip
    assert event.op == "ADD"
    return event.external_id


async def check_lifecycle(p: LiteMemProvider) -> None:
    assert (await p.health()).status == "ok"
    scope = Scope(NS, user_id="u1")
    eid = await _push(p, "The user lives in Beijing", scope, "s-1")
    assert await _push(p, "The user lives in Beijing", scope, "s-1") == eid  # a re-push: same

    hits = await p.search("Beijing", scope, top_k=5)
    assert [h.external_id for h in hits] == [eid]

    await p.update(eid, "The user lives in Beijing, near the river")
    record = await p.get(eid)
    assert record is not None and record.content == "The user lives in Beijing, near the river"
    assert record.scope == scope and record.metadata["steward_id"] == "s-1"

    await p.delete(eid)
    await p.delete(eid)  # idempotent
    assert await p.get(eid) is None
    assert await p.search("Beijing", scope, top_k=5) == []


async def check_scopes(p: LiteMemProvider) -> None:
    mine = await _push(p, "likes green tea", Scope(NS, "alice"), "a")
    await _push(p, "likes green tea", Scope(NS, "bob"), "b")
    shared = await _push(p, "the office serves green tea", Scope(NS), "s")
    await _push(p, "likes green tea", Scope(OTHER_NS, "alice"), "x")

    found = {h.external_id for h in await p.search("green tea", Scope(NS, "alice"), top_k=10)}
    assert found == {mine, shared}  # own + shared; never bob's, never another namespace's
    anonymous = {h.external_id for h in await p.search("green tea", Scope(NS), top_k=10)}
    assert anonymous == {shared}
    listed = {r.external_id for r in await p.list(Scope(NS, "bob"))}
    assert len(listed) == 1 and mine not in listed
    [shared_record] = await p.list(Scope(NS))
    assert shared_record.scope.user_id is None


async def check_resolution(p: LiteMemProvider) -> None:
    """The engine decides: a move supersedes, reported as an ADD that names what it replaced."""
    scope = Scope(NS, user_id="carol")
    [first] = await p.add([Message("user", "我住北京")], scope, idempotency_key="ep-1")
    assert (first.op, first.replaces) == ("ADD", None)
    [moved] = await p.add([Message("user", "我搬到上海了")], scope, idempotency_key="ep-2")
    assert (moved.op, moved.replaces) == ("ADD", first.external_id)
    again = await p.add([Message("user", "我搬到上海了")], scope, idempotency_key="ep-2")
    assert again == [moved]  # replayed by key: no second write
    found = [h.external_id for h in await p.search("北京 上海", scope, top_k=5)]
    assert moved.external_id in found and first.external_id not in found  # superseded: gone


async def test_lite_mem_in_process(lite: Any) -> None:
    p = _in_process(lite)
    await check_lifecycle(p)
    await check_scopes(p)
    await check_resolution(p)


async def test_lite_mem_over_http(lite: Any) -> None:
    http, token = _app(lite, ["steward/*"])
    async with http:
        p = _over_http(http, token)
        await check_lifecycle(p)
        await check_scopes(p)
        await check_resolution(p)


async def test_lite_mem_over_http_refuses_a_key_for_other_namespaces(lite: Any) -> None:
    http, token = _app(lite, ["someone-else/*"])
    async with http:
        with pytest.raises(ProviderUnavailable, match="HTTP 403"):
            await _push(_over_http(http, token), "x", Scope(NS, "u1"), "s")
        with pytest.raises(ProviderUnavailable, match="HTTP 401"):
            await _over_http(http, None).search("x", Scope(NS, "u1"), top_k=1)


def test_lite_mem_end_to_end_through_governance(migrated_url: str, lite: Any) -> None:
    """The whole path, real on both sides: episodes are extracted and resolved by lite-mem (over
    its HTTP API); llm-steward mirrors the facts and lite-mem's decision — the move supersedes the
    old city in llm-steward's own records — and /v1/retrieve returns only the new one."""
    from fastapi.testclient import TestClient

    from llm_steward.main import create_app
    from tests.conftest import AppClient, test_settings
    from tests.fakes.embedder import FakeEmbedder

    http, token = _app(lite, ["steward/*"])
    settings = test_settings(
        migrated_url, memory_provider="litemem", litemem_url="http://lite-mem",
        litemem_api_key=token,
    )  # fmt: skip
    app = create_app(settings, http=http, embedder=FakeEmbedder())
    with TestClient(app) as client:
        c = AppClient(client, app.state.services)
        h = c.new_key("acme")

        def episode(text: str) -> list[dict[str, Any]]:
            body = {"messages": [{"role": "user", "content": text}], "user_id": "u1",
                    "mode": "sync"}  # fmt: skip
            response = client.post("/v1/episodes", json=body, headers=h)
            assert response.status_code == 200, response.text
            return response.json()["memories"]  # type: ignore[no-any-return]

        [beijing] = episode("我住北京")
        [shanghai] = episode("我搬到上海了")
        old = client.get(f"/v1/memories/{beijing['id']}", headers=h).json()
        assert old["status"] == "superseded"
        chain = client.get(f"/v1/memories/{beijing['id']}/history", headers=h).json()["chain"]
        assert [m["id"] for m in chain] == [beijing["id"], shanghai["id"]]
        body = {"query": "北京 上海", "user_id": "u1", "scope": {"user_id": "u1"}}
        found = client.post("/v1/retrieve", json=body, headers=h).json()
        assert found["degraded"] == []
        assert [i["content"] for i in found["items"]] == ["我搬到上海了"]
