"""The Python SDK (`llm-steward-client`, design §7) against the real app: the README flow, async
parity, retried writes applied once, and embedded mode. Both dialects (embedded: SQLite)."""

import asyncio
import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from llm_steward_client import (
    AsyncSteward,
    AuthenticationError,
    NotFoundError,
    PermissionDeniedError,
    Steward,
    models,
)

from llm_steward.errors import ProviderUnavailable
from llm_steward.models import Memory
from tests.conftest import AppClient, Tenant, bearer, running_app
from tests.fakes.embedder import FakeEmbedder
from tests.fakes.llm import FakeLLM
from tests.fakes.memory_provider import FakeMemoryProvider

SPEC = Path(__file__).resolve().parents[3] / "sdk" / "python" / "openapi.json"


@pytest.fixture
def pipeline(migrated_url: str) -> Iterator[AppClient]:
    """Extraction with a fake engine, so `episodes.add` makes memories (as in the README)."""
    with running_app(migrated_url, memory_provider=FakeMemoryProvider(), llm=FakeLLM()) as c:
        yield c


def test_the_readme_flow(pipeline: AppClient) -> None:
    c = pipeline
    t = Tenant(c, "acme")
    st = Steward(api_key=bearer(t.host), http_client=c.http)
    res = st.episodes.add(
        messages=[{"role": "user", "content": "我住上海"}], user_id="u_123", mode="sync"
    )
    assert isinstance(res, models.EpisodeAccepted)
    assert [mem.content for mem in res.memories or []] == ["我住上海"]
    c.drain()
    ctx = st.retrieve("我住哪里 上海", user_id="u_123", format="context")
    assert ctx.context is not None and "我住上海" in ctx.context

    collection = Steward(api_key=bearer(t.reviewer), http_client=c.http).kb.collections.create(
        "faq"
    )
    written = st.kb.entries.create(
        collection.id, "Gift wrap", "Gift wrapping is free", risk_level="medium", user_id="u_123"
    )
    assert written.candidate is not None  # a card for the user
    decided = st.candidates.accept(written.candidate.id, user_id="u_123", scope="team")
    assert decided.entry is not None and decided.entry.status == "active"


def test_errors_are_typed_and_a_user_token_reaches_only_its_user(app_client: AppClient) -> None:
    h, _ = app_client.new_tenant("acme")
    st = Steward(api_key=bearer(h), http_client=app_client.http)
    mine = st.memories.create("likes green tea", user_id="u1")
    st.memories.create("likes black coffee", user_id="u2")
    token = st.tokens.user("u1").token
    as_u1 = Steward(api_key=token, http_client=app_client.http)
    assert [m_.id for m_ in as_u1.memories.list().items] == [mine.id]
    with pytest.raises(PermissionDeniedError):
        as_u1.memories.list(user_id="u2")
    with pytest.raises(NotFoundError):
        st.memories.get("01890000-0000-7000-8000-000000000000")
    with pytest.raises(AuthenticationError):
        Steward(api_key="stw_nope_nope", http_client=app_client.http).memories.list()


def test_a_retried_write_is_applied_once(
    app_client: AppClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The SDK's Idempotency-Key and the server's replay together: a write whose first attempt
    failed with 503 is retried by the client and lands exactly once."""
    h, _ = app_client.new_tenant("acme")
    memories = app_client.services.memories
    create, failures = memories.create, [ProviderUnavailable("engine busy")]

    async def busy_once(*args: Any, **kwargs: Any) -> Any:
        if failures:
            raise failures.pop()
        return await create(*args, **kwargs)

    monkeypatch.setattr(memories, "create", busy_once)
    monkeypatch.setattr(time, "sleep", lambda _: None)
    st = Steward(api_key=bearer(h), http_client=app_client.http)
    st.memories.create("likes green tea", user_id="u1")
    assert failures == [] and app_client.count(Memory) == 1


def test_the_async_client_works_the_same(app_client: AppClient) -> None:
    h, _ = app_client.new_tenant("acme")

    async def flow() -> tuple[str, list[str]]:
        transport = httpx.ASGITransport(app=app_client.http.app)
        http = httpx.AsyncClient(transport=transport, base_url="http://steward")
        async with AsyncSteward(api_key=bearer(h), http_client=http) as st:
            created = await st.memories.create("likes green tea", user_id="u1")
            page = await st.memories.list(user_id="u1")
            return created.content, [m_.content for m_ in page.items]

    content, listed = app_client.call(flow)
    assert content == "likes green tea" and listed == ["likes green tea"]


def test_the_committed_spec_is_the_apps(app_client: AppClient) -> None:
    """The SDK is generated from sdk/python/openapi.json: an API change must regenerate it
    (sdk/python/scripts/generate.py)."""
    current = json.dumps(app_client.http.app.openapi(), indent=2, sort_keys=True)  # type: ignore[attr-defined]
    assert SPEC.read_text() == current + "\n", "stale: run sdk/python/scripts/generate.py"


# --- embedded mode -------------------------------------------------------------------------------


def _until_synced(get: Any, memory_id: Any) -> None:
    for _ in range(200):
        if get(memory_id).sync_status == "synced":
            return
        time.sleep(0.05)
    raise AssertionError("the in-process worker never synced the memory")


def test_embedded_runs_in_process_on_sqlite(tmp_path: Path) -> None:
    url = f"sqlite+aiosqlite:///{tmp_path / 'steward.db'}"
    st = Steward.embedded(url, app_overrides={"embedder": FakeEmbedder()})
    try:
        memory = st.memories.create("Ada prefers green tea", user_id="ada")
        _until_synced(st.memories.get, memory.id)  # the outbox worker runs in this process
        found = st.retrieve("green tea", user_id="ada", format="context")
        assert found.context is not None and "Ada prefers green tea" in found.context
    finally:
        st.close()
    again = Steward.embedded(url, app_overrides={"embedder": FakeEmbedder()})
    try:  # a second start: same database, same namespace, nothing lost
        assert [m_.content for m_ in again.memories.list(user_id="ada").items] == [
            "Ada prefers green tea"
        ]
    finally:
        again.close()


def test_async_embedded(tmp_path: Path) -> None:
    url = f"sqlite+aiosqlite:///{tmp_path / 'steward.db'}"

    async def flow() -> list[str]:
        st = await AsyncSteward.embedded(url, app_overrides={"embedder": FakeEmbedder()})
        async with st:
            await st.memories.create("Bob likes coffee", user_id="bob")
            return [m_.content for m_ in (await st.memories.list(user_id="bob")).items]

    assert asyncio.run(flow()) == ["Bob likes coffee"]
