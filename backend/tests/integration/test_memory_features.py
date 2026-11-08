"""P2 memory features on both dialects: profile keys, pinned memories, history, host_app actor,
the active-profile-key index, and reconcile."""

import uuid
from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from llm_steward.models import Memory
from llm_steward.models.base import utcnow
from llm_steward.models.enums import MemoryOrigin, MemoryStatus
from llm_steward.providers.native.providers import NativeMemoryProvider
from tests.conftest import AppClient


def _post(c: AppClient, h: dict[str, str], **body: Any) -> dict[str, Any]:
    response = c.http.post("/v1/memories", json=body, headers=h)
    assert response.status_code == 201, response.text
    return response.json()  # type: ignore[no-any-return]


def test_a_new_profile_value_supersedes_the_old_one(app_client: AppClient) -> None:
    h = app_client.new_key("acme")
    beijing = _post(app_client, h, content="北京", user_id="u1", key="user.city")
    shanghai = _post(app_client, h, content="上海", user_id="u1", key="user.city")
    _post(app_client, h, content="杭州", user_id="u2", key="user.city")  # another user: untouched

    old = app_client.http.get(f"/v1/memories/{beijing['id']}", headers=h).json()
    assert (old["status"], old["memory_type"]) == ("superseded", "profile")
    profile = app_client.http.get("/v1/profile?user_id=u1", headers=h).json()
    assert profile["values"] == {"user.city": "上海"}
    chain = app_client.http.get(f"/v1/memories/{shanghai['id']}/history", headers=h).json()["chain"]
    assert [m["content"] for m in chain] == ["北京", "上海"]


def test_a_profile_value_cannot_be_session_scoped(app_client: AppClient) -> None:
    """Codex P2 #3: a profile key holds one current value per user (and agent); a session-scoped
    value would silently supersede across sessions, so it is refused."""
    h = app_client.new_key("acme")
    body = {"content": "北京", "user_id": "u1", "session_id": "s1", "key": "user.city"}
    response = app_client.http.post("/v1/memories", json=body, headers=h)
    assert response.status_code == 422
    assert "session" in response.json()["detail"]


async def test_the_database_allows_one_active_value_per_profile_key(
    sessions: async_sessionmaker[AsyncSession], namespace_id: uuid.UUID
) -> None:
    def row(user_id: str | None) -> Memory:
        return Memory(
            namespace_id=namespace_id,
            user_id=user_id,
            memory_type="profile",
            key="user.city",
            content="x",
            status=MemoryStatus.ACTIVE,
            origin=MemoryOrigin.DIRECT,
            valid_from=utcnow(),
            content_hash="h",
            tags=[],
            meta={},
        )

    async with sessions() as s, s.begin():
        s.add_all([row("u1"), row(None)])
    with pytest.raises(IntegrityError):  # NULL scope parts compare equal (COALESCE in the index)
        async with sessions() as s, s.begin():
            s.add(row(None))


def test_profile_shows_exactly_the_core_memories_retrieve_puts_first(
    app_client: AppClient,
) -> None:
    h = app_client.new_key("acme")
    _post(app_client, h, content="zh", key="user.language")  # shared default
    _post(app_client, h, content="en", user_id="u1", key="user.language")  # u1 overrides it
    _post(app_client, h, content="Paris", user_id="u1", key="user.city")
    rule = _post(app_client, h, content="no emojis", user_id="u1", pinned=True)
    _post(app_client, h, content="bot-only", user_id="u1", agent_id="bot", pinned=True)
    app_client.drain()

    profile = app_client.http.get("/v1/profile?user_id=u1", headers=h).json()
    assert profile["values"] == {"user.language": "en", "user.city": "Paris"}
    assert [m["id"] for m in profile["pinned"]] == [rule["id"]]  # not the agent-scoped one
    body = {"query": "nothing matches this", "scope": {"user_id": "u1"}}
    items = app_client.http.post("/v1/retrieve", json=body, headers=h).json()["items"]
    assert sorted(i["content"] for i in items) == sorted(["zh", "en", "Paris", "no emojis"])


def test_pinned_memories_lead_every_retrieval(app_client: AppClient) -> None:
    h = app_client.new_key("acme")
    fact = _post(app_client, h, content="prefers short answers in Chinese", user_id="u1")
    _post(app_client, h, content="likes green tea", user_id="u1")
    patched = app_client.http.patch(
        f"/v1/memories/{fact['id']}", json={"pinned": True, "importance": 0.9}, headers=h
    ).json()
    assert (patched["pinned"], patched["importance"]) == (True, 0.9)
    app_client.drain()

    body = {"query": "green tea", "scope": {"user_id": "u1"}}
    items = app_client.http.post("/v1/retrieve", json=body, headers=h).json()["items"]
    assert items[0]["id"] == fact["id"] and items[0]["pinned"] is True
    assert [i["content"] for i in items[1:]].count("prefers short answers in Chinese") == 0
    other = {"query": "green tea", "scope": {"user_id": "u2"}}
    assert app_client.http.post("/v1/retrieve", json=other, headers=h).json()["items"] == []


def test_api_key_writes_are_attributed_to_the_host_app(app_client: AppClient) -> None:
    h = app_client.new_key("acme")
    memory = _post(app_client, h, content="x")
    [change] = app_client.http.get(f"/v1/memories/{memory['id']}/history", headers=h).json()[
        "changes"
    ]
    assert change["actor_type"] == "host_app" and change["actor_id"].startswith("api_key:")


def test_reconcile_requeues_what_the_provider_lost_and_reports_orphans(
    app_client: AppClient,
) -> None:
    from llm_steward.providers.base import Message, Scope

    h, ns = app_client.new_tenant("acme")
    _post(app_client, h, content="kept", user_id="u1")
    lost = _post(app_client, h, content="lost", user_id="u1")
    app_client.drain()
    provider = app_client.services.providers.memory
    assert isinstance(provider, NativeMemoryProvider)

    async def tamper() -> None:
        await provider.delete(lost["id"])  # the engine lost it
        await provider.add([Message("user", "stray")], Scope(ns, "u1"), infer=False)  # an orphan

    app_client.call(tamper)
    report = app_client.call(app_client.services.reconcile.run, ns)
    assert report.requeued == [uuid.UUID(lost["id"])]
    assert (len(report.orphans), report.checked) == (1, 2)
    app_client.drain()
    back = app_client.http.get(f"/v1/memories/{lost['id']}", headers=h).json()
    assert back["sync_status"] == "synced"
