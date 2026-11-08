"""P2 acceptance on both dialects, through the HTTP API: episodes → extraction (ADD-only, like
mem0 2.2.1) → conflict compensation → retrieval now and `as_of`. Fake engine, fake LLM."""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import update

from llm_steward.models import Job
from llm_steward.models.base import utcnow
from llm_steward.providers.base import RESOLVES_CONFLICTS
from llm_steward.services.extraction_service import ExtractionService
from llm_steward.services.worker import JobNotClaimable
from tests.conftest import AppClient, running_app
from tests.fakes.llm import FakeLLM, update_when
from tests.fakes.memory_provider import EXTRACTED_BY, FakeMemoryProvider


@pytest.fixture
def pipeline(migrated_url: str) -> Iterator[tuple[AppClient, FakeMemoryProvider, FakeLLM]]:
    engine, llm = FakeMemoryProvider(), FakeLLM(memory_resolve=update_when("搬"))
    with running_app(migrated_url, memory_provider=engine, llm=llm) as client:
        yield client, engine, llm


def _episode(c: AppClient, h: dict[str, str], text: str, **body: Any) -> dict[str, Any]:
    body = {"messages": [{"role": "user", "content": text}], "user_id": "u1", **body}
    response = c.http.post("/v1/episodes", json=body, headers=h)
    assert response.status_code in (200, 202), response.text
    return response.json()  # type: ignore[no-any-return]


def _retrieve(c: AppClient, h: dict[str, str], **body: Any) -> list[str]:
    response = c.http.post("/v1/retrieve", json={"user_id": "u1", **body}, headers=h)
    assert response.status_code == 200, response.text
    return [i["content"] for i in response.json()["items"]]


def test_golden_beijing_then_shanghai(
    pipeline: tuple[AppClient, FakeMemoryProvider, FakeLLM],
) -> None:
    c, _, llm = pipeline
    h = c.new_key("acme")
    first = _episode(c, h, "我住北京", mode="sync")
    [beijing] = first["memories"]
    assert (first["status"], beijing["content"], beijing["origin"]) == (
        "processed",
        "我住北京",
        "extraction",
    )
    before_move = datetime.now(UTC).isoformat()

    second = _episode(c, h, "我搬到上海了", mode="sync")
    [shanghai] = second["memories"]
    assert shanghai["status"] == "active"

    old = c.http.get(f"/v1/memories/{beijing['id']}", headers=h).json()
    assert old["status"] == "superseded" and old["valid_to"] is not None
    history = c.http.get(f"/v1/memories/{beijing['id']}/history", headers=h).json()
    assert [m["content"] for m in history["chain"]] == ["我住北京", "我搬到上海了"]
    supersede = history["changes"][-1]
    assert supersede["actor_type"] == "ai" and supersede["actor_id"] == "memory.resolve:fake-model"
    assert supersede["reason"].startswith("UPDATE")

    scope = {"scope": {"user_id": "u1"}}
    assert _retrieve(c, h, query="我住在哪里 北京 上海", **scope) == ["我搬到上海了"]
    assert _retrieve(c, h, query="我住在哪里 北京 上海", as_of=before_move, **scope) == ["我住北京"]
    [(role, payload)] = llm.calls
    assert role == "memory.resolve" and payload["EXISTING"][0]["fact"] == "我住北京"


def test_a_duplicate_fact_is_dropped(migrated_url: str) -> None:
    llm = FakeLLM(memory_resolve=lambda p: {"action": "NOOP", "target_id": None, "reason": "same"})
    with running_app(migrated_url, memory_provider=FakeMemoryProvider(), llm=llm) as c:
        h = c.new_key("acme")
        _episode(c, h, "我喜欢绿茶", mode="sync")
        assert _episode(c, h, "我喜欢绿茶", mode="sync")["memories"] == []
        active = c.http.get("/v1/memories?user_id=u1", headers=h).json()["items"]
        assert [m["content"] for m in active] == ["我喜欢绿茶"]
        c.drain()  # the duplicate's removal from the engine


def test_async_episodes_buffer_until_large_enough(
    pipeline: tuple[AppClient, FakeMemoryProvider, FakeLLM],
) -> None:
    c, engine, _ = pipeline
    h = c.new_key("acme")
    assert _episode(c, h, "短句")["status"] == "buffered"
    assert engine.add_calls == 0
    flushed = c.http.post("/v1/episodes/flush", json={"user_id": "u1"}, headers=h).json()
    assert flushed["job_id"] is not None
    assert (
        c.http.post("/v1/episodes/flush", json={"user_id": "u1"}, headers=h).json()["job_id"]
        is None
    )
    c.drain()
    assert engine.add_calls == 1
    assert [m["content"] for m in c.http.get("/v1/memories", headers=h).json()["items"]] == ["短句"]


def test_a_large_episode_flushes_by_itself(
    pipeline: tuple[AppClient, FakeMemoryProvider, FakeLLM],
) -> None:
    c, _, _ = pipeline
    h = c.new_key("acme")
    assert _episode(c, h, "长" * 1100)["status"] == "queued"  # over the 1024-token buffer


def test_native_cannot_take_episodes(app_client: AppClient) -> None:
    h = app_client.new_key("acme")
    body = {"messages": [{"role": "user", "content": "x"}]}
    response = app_client.http.post("/v1/episodes", json=body, headers=h)
    assert response.status_code == 422
    assert "POST /v1/memories" in response.json()["detail"]


def test_extracted_memories_record_the_role_and_model_that_produced_them(
    pipeline: tuple[AppClient, FakeMemoryProvider, FakeLLM],
) -> None:
    c, _, _ = pipeline
    h = c.new_key("acme")
    [memory] = _episode(c, h, "我喜欢绿茶", mode="sync")["memories"]
    [create] = c.http.get(f"/v1/memories/{memory['id']}/history", headers=h).json()["changes"]
    assert (create["op"], create["actor_type"], create["actor_id"]) == (
        "create",
        "ai",
        EXTRACTED_BY,
    )


def test_an_engine_that_resolves_conflicts_itself_gets_no_compensation(migrated_url: str) -> None:
    engine = FakeMemoryProvider()
    engine.capabilities = engine.capabilities | {RESOLVES_CONFLICTS}
    llm = FakeLLM()  # no answer scripted: any memory.resolve call would fail the test
    with running_app(migrated_url, memory_provider=engine, llm=llm) as c:
        h = c.new_key("acme")
        _episode(c, h, "我住北京", mode="sync")
        assert len(_episode(c, h, "我搬到上海了", mode="sync")["memories"]) == 1
        assert llm.calls == []


def test_an_engine_that_needs_compensation_cannot_run_without_a_resolver() -> None:
    with pytest.raises(ValueError, match="needs conflict compensation"):
        ExtractionService(None, None, FakeMemoryProvider(), None)  # type: ignore[arg-type]


def test_a_retried_extraction_does_not_add_to_the_engine_twice(migrated_url: str) -> None:
    """Codex P2 #1: the resolve call fails after the mirror committed; the retry must resume
    from the mirrored facts instead of extracting (and duplicating) them again."""
    calls = {"n": 0}
    moved = update_when("搬")

    def flaky(payload: dict[str, Any]) -> dict[str, Any]:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("model endpoint down")
        return moved(payload)

    engine = FakeMemoryProvider()
    with running_app(migrated_url, memory_provider=engine, llm=FakeLLM(memory_resolve=flaky)) as c:
        h = c.new_key("acme")
        _episode(c, h, "我住北京", mode="sync")
        failed = c.http.post(
            "/v1/episodes",
            json={
                "messages": [{"role": "user", "content": "我搬到上海了"}],
                "user_id": "u1",
                "mode": "sync",
            },
            headers=h,
        )
        assert failed.status_code == 503
        c.call(_due_now, c)
        c.drain()
        assert engine.add_calls == 2  # once per episode, never again on the retry
        active = c.http.get("/v1/memories?user_id=u1", headers=h).json()["items"]
        assert [m["content"] for m in active] == ["我搬到上海了"]
        assert sorted(r.content for r in engine.records.values()) == ["我住北京", "我搬到上海了"]


async def _due_now(c: AppClient) -> None:
    """Skip the retry backoff."""
    async with c.services.engine.begin() as conn:
        await conn.execute(update(Job).values(run_after=utcnow()))


def test_sync_mode_reports_queued_when_the_worker_claimed_the_job_first(
    pipeline: tuple[AppClient, FakeMemoryProvider, FakeLLM], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex P2 #2: the worker may claim the flushed job before the request does."""
    c, _, _ = pipeline
    h = c.new_key("acme")

    async def taken(job_id: uuid.UUID) -> Any:
        raise JobNotClaimable(job_id)

    monkeypatch.setattr(c.services.episodes, "_run_job", taken)
    body = _episode(c, h, "我住北京", mode="sync")
    assert (body["status"], body["memories"]) == ("queued", [])
    assert body["job_id"] is not None
