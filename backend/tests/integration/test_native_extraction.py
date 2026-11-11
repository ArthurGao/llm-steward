"""llm-steward extracts memories itself for an engine that cannot (`STEWARD_MEMORY_LLM`, design §5
step 2): the native engine then takes conversations like mem0 does. Both dialects, through the API;
FakeLLM at the model boundary."""

from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import update

from llm_steward.llm.roles import LLMConfigError
from llm_steward.models import Job
from llm_steward.models.base import utcnow
from llm_steward.models.enums import JobType
from tests.conftest import AppClient, running_app
from tests.fakes.llm import FakeLLM, update_when, user_lines

ON = {"memory_llm": True}


@pytest.fixture
def native(migrated_url: str) -> Iterator[tuple[AppClient, FakeLLM]]:
    llm = FakeLLM(memory_extract=user_lines(), memory_resolve=update_when("搬"))
    with running_app(migrated_url, settings=ON, llm=llm) as client:
        yield client, llm


def _episode(c: AppClient, h: dict[str, str], *texts: str, **body: Any) -> Any:
    messages = [{"role": "user", "content": t} for t in texts]
    body = {"messages": messages, "user_id": "u1", "mode": "sync", **body}
    return c.http.post("/v1/episodes", json=body, headers=h)


def _active(c: AppClient, h: dict[str, str]) -> list[str]:
    items = c.http.get("/v1/memories?user_id=u1", headers=h).json()["items"]
    return sorted(m["content"] for m in items if m["status"] == "active")


def test_a_conversation_becomes_memories_extracted_by_llm_steward(
    native: tuple[AppClient, FakeLLM],
) -> None:
    c, llm = native
    h = c.new_key("acme")
    r = _episode(c, h, "我住北京", "我喜欢绿茶")
    assert r.status_code == 200, r.text
    memories = r.json()["memories"]
    assert sorted(m["content"] for m in memories) == sorted(["我住北京", "我喜欢绿茶"])
    assert {m["origin"] for m in memories} == {"extraction"}
    [create] = c.http.get(f"/v1/memories/{memories[0]['id']}/history", headers=h).json()["changes"]
    assert (create["actor_type"], create["actor_id"]) == ("ai", "memory.extract:fake-model")
    assert [role for role, _ in llm.calls] == ["memory.extract"]  # nothing related yet: no resolve

    assert len(c.jobs(JobType.MEMORY_UPSERT)) == 2  # pushed to the engine by the Syncer
    c.drain()
    found = c.retrieve(h, query="绿茶", scope={"user_id": "u1"}, sources=["memory"])
    assert "我喜欢绿茶" in [i["content"] for i in found["items"]]


def test_a_later_fact_supersedes_the_one_it_replaces(native: tuple[AppClient, FakeLLM]) -> None:
    c, llm = native
    h = c.new_key("acme")
    _episode(c, h, "我住北京")
    c.drain()  # the engine holds the first fact, so compensation can find it
    assert _episode(c, h, "我搬到上海了").status_code == 200
    assert _active(c, h) == ["我搬到上海了"]
    assert [role for role, _ in llm.calls] == ["memory.extract", "memory.extract", "memory.resolve"]


def test_a_retry_resumes_from_the_mirror_and_extracts_once(migrated_url: str) -> None:
    calls = {"n": 0}
    moved = update_when("搬")

    def flaky(payload: dict[str, Any]) -> dict[str, Any]:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("model endpoint down")
        return moved(payload)

    llm = FakeLLM(memory_extract=user_lines(), memory_resolve=flaky)
    with running_app(migrated_url, settings=ON, llm=llm) as c:
        h = c.new_key("acme")
        _episode(c, h, "我住北京")
        c.drain()
        assert _episode(c, h, "我搬到上海了").status_code == 503  # the resolve call failed

        async def due_now() -> None:
            async with c.services.engine.begin() as conn:
                await conn.execute(update(Job).values(run_after=utcnow()))

        c.call(due_now)
        c.drain()
        assert [role for role, _ in llm.calls].count("memory.extract") == 2  # once per episode
        assert _active(c, h) == ["我搬到上海了"]


def test_the_kb_suggestion_step_still_follows(migrated_url: str) -> None:
    llm = FakeLLM(memory_extract=user_lines())
    with running_app(migrated_url, settings={**ON, "kb_llm": True}, llm=llm) as c:
        h = c.new_key("acme")
        assert _episode(c, h, "我住北京").status_code == 200
        assert len(c.jobs(JobType.KB_SUGGEST)) == 1


def test_off_by_default_native_still_refuses_conversations(app_client: AppClient) -> None:
    h = app_client.new_key("acme")
    r = _episode(app_client, h, "我住北京")
    assert r.status_code == 422 and "STEWARD_MEMORY_LLM" in r.json()["detail"]


def test_switched_on_without_a_model_the_app_does_not_start(migrated_url: str) -> None:
    with pytest.raises(LLMConfigError, match=r"memory\.extract"):
        with running_app(migrated_url, settings=ON):
            pass
