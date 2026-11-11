"""`/admin/v1` (design §9, P6a): one static admin token, no login; the Admin's reads and the
operator actions, each inside one namespace. Both dialects."""

import asyncio
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import update

from llm_steward.models import ExternalRef, Job, KbEntry
from llm_steward.models.enums import JobStatus, KbStatus, SyncStatus
from tests.conftest import AppClient, Tenant, post_memory, running_app

TOKEN = "admin-token-for-tests-0123456789abcdef"
ADMIN = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def c(migrated_url: str) -> Iterator[AppClient]:
    with running_app(migrated_url, settings={"admin_token": TOKEN}) as client:
        yield client


@pytest.fixture
def t(c: AppClient) -> Tenant:
    return Tenant(c, "acme")


def get(c: AppClient, path: str, **params: Any) -> Any:
    r = c.http.get(f"/admin/v1{path}", params=params, headers=ADMIN)
    assert r.status_code == 200, r.text
    return r.json()


def post(c: AppClient, path: str, body: Any = None, status: int = 200, **params: Any) -> Any:
    r = c.http.post(f"/admin/v1{path}", json=body, params=params, headers=ADMIN)
    assert r.status_code == status, r.text
    return r.json()


# --- access ------------------------------------------------------------------------------------


def test_without_a_token_configured_the_admin_api_is_off(migrated_url: str) -> None:
    with running_app(migrated_url) as client:
        r = client.http.get("/admin/v1/namespaces", headers=ADMIN)
        assert r.status_code == 503


def test_the_token_is_checked_before_anything_else(c: AppClient, t: Tenant) -> None:
    for headers in ({}, {"Authorization": "Bearer wrong-token-of-the-same-length-0000000"}):
        for path in ("/namespaces", "/ns/acme/stats", "/ns/no-such-namespace/stats"):
            r = c.http.get(f"/admin/v1{path}", headers=headers)
            assert r.status_code == 401, path  # never 404: no probing for namespaces
    # an API key, even with every scope, is not the admin token
    assert c.http.get("/admin/v1/namespaces", headers=t.reviewer).status_code == 401


def test_every_admin_route_declares_the_admin_token(c: AppClient) -> None:
    spec = c.http.app.openapi()  # type: ignore[attr-defined]
    admin = {
        (method, path): operation.get("security", [])
        for path, operations in spec["paths"].items()
        if path.startswith("/admin/v1")
        for method, operation in operations.items()
    }
    assert len(admin) >= 30
    assert all(any("AdminToken" in r for r in sec) for sec in admin.values())


# --- dashboard, users ----------------------------------------------------------------------------


def test_stats_count_what_is_there(c: AppClient, t: Tenant) -> None:
    kept = post_memory(c, t.host, "likes green tea", user_id="u1")
    gone = post_memory(c, t.host, "old address", user_id="u1")
    c.http.delete(f"/v1/memories/{gone}", headers=t.host)
    collection = t.collection()
    t.submit(collection, "A refund law", risk_level="high")  # waits for review
    c.retrieve(t.host, query="zebra migration patterns", sources=["kb"])  # finds nothing
    c.drain()
    found = c.retrieve(t.host, query="green tea", sources=["memory"], scope={"user_id": "u1"})
    assert found["items"]  # this one is not a gap
    stats = get(c, "/ns/acme/stats")
    assert (stats["memories"]["active"], stats["memories"]["deleted"]) == (1, 1)
    assert stats["pending_reviews"] == 1
    assert [g["query"] for g in stats["gaps"]] == ["zebra migration patterns"]
    assert len(stats["writes_7d"]) == 7 and sum(d["count"] for d in stats["writes_7d"]) >= 3
    assert sum(d["count"] for d in stats["retrievals_7d"]) == 2
    assert kept


def test_retrieval_logs_carry_the_total_latency_and_what_degraded(
    c: AppClient, t: Tenant, monkeypatch: pytest.MonkeyPatch
) -> None:
    post_memory(c, t.host, "likes green tea", user_id="u1")
    t.submit(t.collection(default_risk="low"), "Green tea ships in tins")  # synced: kb is searched
    c.drain()
    search = c.services.providers.memory.search

    async def slow(*args: Any, **kwargs: Any) -> Any:
        await asyncio.sleep(0.06)
        return await search(*args, **kwargs)

    async def broken(*args: Any, **kwargs: Any) -> Any:
        raise ConnectionError("engine down")

    monkeypatch.setattr(c.services.providers.memory, "search", slow)
    monkeypatch.setattr(c.services.providers.kb, "search", broken)
    c.retrieve(t.host, query="green tea", sources=["memory", "kb"], scope={"user_id": "u1"})
    [log] = get(c, "/ns/acme/retrieval-logs")["items"]
    assert log["latency_ms"] >= 60
    assert log["degraded"] == ["kb"]
    assert get(c, "/ns/acme/stats")["retrieval_p95_ms"] >= 60


def test_users_roll_up_and_can_be_erased(c: AppClient, t: Tenant) -> None:
    post_memory(c, t.host, "u1 likes tea", user_id="u1", pinned=True)
    post_memory(c, t.host, "u1 lives in Oslo", user_id="u1")
    post_memory(c, t.host, "u2 likes coffee", user_id="u2")
    users = {u["user_id"]: u for u in get(c, "/ns/acme/users")["items"]}
    assert (users["u1"]["memory_count"], users["u1"]["pinned_count"]) == (2, 1)
    detail = get(c, "/ns/acme/users/u1")
    assert [p["content"] for p in detail["pinned"]] == ["u1 likes tea"]
    r = c.http.delete("/admin/v1/ns/acme/memories", params={"user_id": "u1"}, headers=ADMIN)
    assert r.status_code == 200 and r.json()["memories"] == 2
    assert [u["user_id"] for u in get(c, "/ns/acme/users")["items"]] == ["u2"]
    assert c.http.get("/admin/v1/ns/acme/users/u1", headers=ADMIN).status_code == 404


# --- review and knowledge ------------------------------------------------------------------------


def _queued(t: Tenant, title: str = "Refund law") -> str:
    r = t.submit(t.collection(), "Refunds are owed within 14 days", title=title, risk_level="high")
    assert r.status_code == 202, r.text
    return str(r.json()["candidate"]["id"])


def test_the_admin_reviews_the_queue(c: AppClient, t: Tenant) -> None:
    approved, rejected = _queued(t), _queued(t, "Another law")
    queue = get(c, "/ns/acme/candidates", stage="awaiting_review")["items"]
    assert {q["id"] for q in queue} == {approved, rejected}
    assert get(c, f"/ns/acme/candidates/{approved}")["risk_level"] == "high"
    decided = post(c, f"/ns/acme/candidates/{approved}/accept", {})
    assert decided["entry"]["status"] == "verified"
    assert decided["entry"]["verified_by"] == "admin"
    r = c.http.post(f"/admin/v1/ns/acme/candidates/{rejected}/reject", json={}, headers=ADMIN)
    assert r.status_code == 422  # a rejection says why
    post(c, f"/ns/acme/candidates/{rejected}/reject", {"note": "not our policy"})
    assert get(c, "/ns/acme/candidates", stage="awaiting_review")["items"] == []


def test_an_entry_has_its_versions_newest_first(c: AppClient, t: Tenant) -> None:
    r = t.submit(t.collection(), "Refunds take 5 days", title="Refund time")
    entry = r.json()["entry"]["id"]
    edited = c.http.patch(
        f"/admin/v1/ns/acme/kb/entries/{entry}",
        json={"content": "Refunds take 3 days"},
        headers=ADMIN,
    )
    assert edited.status_code == 200 and edited.json()["entry"]["version"] == 2
    history = get(c, f"/ns/acme/kb/entries/{entry}/history")
    assert [(v["version"], v["content"]) for v in history["versions"]] == [
        (2, "Refunds take 3 days"),
        (1, "Refunds take 5 days"),
    ]
    collections = get(c, "/ns/acme/kb/collections")["items"]
    assert sum(col["entry_count"] for col in collections) == 1
    deprecated = post(c, f"/ns/acme/kb/entries/{entry}/deprecate", reason="outdated")
    assert deprecated["status"] == "deprecated"


def test_a_playground_gap_goes_to_the_kb_through_review(c: AppClient, t: Tenant) -> None:
    answers = t.collection(default_risk="low", name="answers")
    collection = t.collection(default_risk="low", name="shipping")  # nothing in it yet
    t.submit(answers, "Refunds take five days", title="Refund time")
    c.drain()
    asked = {"query": "refunds take", "sources": ["kb"], "collections": ["answers"]}
    assert post(c, "/ns/acme/retrieve", asked)["items"]  # answered: not a gap
    gap = {"query": "do you ship to Norway", "sources": ["kb"], "collections": ["shipping"]}
    result = post(c, "/ns/acme/retrieve", gap)
    assert result["items"] == []
    [log] = get(c, "/ns/acme/retrieval-logs", gaps="true")["items"]
    assert (log["gap"], log["hit_count"]) == (True, 0)
    body = {
        "collection_id": collection,
        "title": "Shipping to Norway",
        "content": "We ship to Norway.",
    }
    written = post(c, f"/ns/acme/retrieval-logs/{log['id']}/add-to-kb", body, status=202)
    candidate = written["candidate"]
    assert (
        written["entry"] is None and candidate["stage"] == "awaiting_review"
    )  # proposed, not live
    assert candidate["draft"]["source_refs"] == [{"type": "retrieval", "id": log["id"]}]
    accepted = post(c, f"/ns/acme/candidates/{candidate['id']}/accept", {})
    assert accepted["entry"]["status"] == "verified"


def test_an_answer_without_a_collection_goes_to_the_suggestions_collection(
    c: AppClient, t: Tenant
) -> None:
    c.retrieve(t.host, query="do you ship to Norway", sources=["kb"])
    [log] = get(c, "/ns/acme/retrieval-logs")["items"]
    body = {"title": "Shipping to Norway", "content": "We ship to Norway."}
    written = post(c, f"/ns/acme/retrieval-logs/{log['id']}/add-to-kb", body, status=202)
    suggestions = c.call(c.services.kb.suggestion_collection, t.ns)
    assert written["candidate"]["draft"]["collection_id"] == str(suggestions)


def test_an_update_candidate_comes_with_the_entry_it_would_obsolete(
    c: AppClient, t: Tenant
) -> None:
    collection = t.collection(default_risk="low")
    entry = t.post(
        "/v1/kb/entries",
        {"collection_id": collection, "title": "Refund time", "content": "Five days"},
        as_reviewer=True,
    ).json()["entry"]
    r = c.http.patch(f"/v1/kb/entries/{entry['id']}", json={"content": "Ten days"}, headers=t.host)
    candidate = r.json()["candidate"]
    assert candidate["op"] == "UPDATE"
    detail = get(c, f"/ns/acme/candidates/{candidate['id']}")
    assert (detail["target"]["id"], detail["target"]["content"]) == (entry["id"], "Five days")
    added = t.submit(collection, "Refunds are free", risk_level="high").json()["candidate"]
    assert get(c, f"/ns/acme/candidates/{added['id']}")["target"] is None


# --- activity, jobs, providers, sync -------------------------------------------------------------


def test_activity_filters_by_object_and_actor(c: AppClient, t: Tenant) -> None:
    memory = post_memory(c, t.host, "likes tea", user_id="u1")
    candidate = _queued(t)
    post(c, f"/ns/acme/candidates/{candidate}/accept", {})
    by_admin = get(c, "/ns/acme/activity", actor_type="admin")["items"]
    assert by_admin and all(a["actor_type"] == "admin" for a in by_admin)
    mine = get(c, "/ns/acme/activity", object_type="memory", object_id=memory)["items"]
    assert [a["op"] for a in mine] == ["create"]


def _job(c: AppClient, status: JobStatus) -> str:
    from sqlalchemy import select

    async def pick() -> uuid.UUID:
        async with c.sessions() as s, s.begin():
            job = await s.scalar(select(Job).limit(1))
            assert job is not None
            await s.execute(update(Job).where(Job.id == job.id).values(status=status, attempts=8))
            return job.id

    return str(c.call(pick))


def test_jobs_are_cancelled_and_retried_only_from_the_right_state(c: AppClient, t: Tenant) -> None:
    post_memory(c, t.host, "likes tea", user_id="u1")  # a sync job
    job = _job(c, JobStatus.PENDING)
    assert post(c, f"/ns/acme/jobs/{job}/cancel")["status"] == "failed"
    r = c.http.post(f"/admin/v1/ns/acme/jobs/{job}/cancel", headers=ADMIN)
    assert r.status_code == 409  # not pending any more
    retried = post(c, f"/ns/acme/jobs/{job}/retry")
    assert (retried["status"], retried["attempts"]) == ("pending", 0)
    assert [j["id"] for j in get(c, "/ns/acme/jobs", status="pending")["items"]] == [job]


def test_providers_and_a_failed_sync_retried(c: AppClient, t: Tenant) -> None:
    providers = {p["role"]: p for p in get(c, "/ns/acme/providers")["items"]}
    assert set(providers) == {"memory", "kb"} and providers["kb"]["healthy"] is True
    r = t.submit(t.collection(), "Refunds take 5 days")
    entry = r.json()["entry"]["id"]
    c.drain()

    async def fail() -> None:
        async with c.sessions() as s, s.begin():
            await s.execute(
                update(ExternalRef)
                .where(ExternalRef.object_id == uuid.UUID(entry))
                .values(sync_status=SyncStatus.FAILED, last_error="engine down")
            )

    c.call(fail)
    [ref] = get(c, "/ns/acme/sync", status="failed")["items"]
    assert get(c, "/ns/acme/providers")["items"][1]["failed"] == 1
    assert post(c, f"/ns/acme/sync/{ref['id']}/retry")["sync_status"] == "pending"
    c.drain()
    assert get(c, "/ns/acme/sync", status="failed")["items"] == []


# --- settings, keys, isolation -------------------------------------------------------------------


def test_settings_are_validated_before_they_are_saved(c: AppClient, t: Tenant) -> None:
    assert any(tpl["name"] == "kb_draft" for tpl in get(c, "/ns/acme/settings")["templates"])
    put = lambda text: c.http.put(  # noqa: E731
        "/admin/v1/ns/acme/settings", json={"yaml": text}, headers=ADMIN
    )
    assert put("retrieval: {top_k: 0}\n").status_code == 422
    assert put("- not a mapping\n").status_code == 422
    saved = put("retrieval: {top_k: 4}\n")
    assert saved.status_code == 200 and "top_k: 4" in saved.json()["yaml"]
    assert put("retrieval: {top_k: 0}\n").status_code == 422
    assert "top_k: 4" in get(c, "/ns/acme/settings")["yaml"]  # unchanged by the refusal


def test_a_key_is_shown_once_works_and_can_be_revoked(c: AppClient, t: Tenant) -> None:
    created = post(c, "/ns/acme/keys", {"name": "ci", "scopes": ["read"]}, status=201)
    key = {"Authorization": f"Bearer {created['key']}"}
    assert c.http.get("/v1/memories", headers=key).status_code == 200
    listed = {k["id"]: k for k in get(c, "/ns/acme/keys")["items"]}
    assert "key" not in listed[created["id"]] and listed[created["id"]]["scopes"] == ["read"]
    c.http.delete(f"/admin/v1/ns/acme/keys/{created['id']}", headers=ADMIN)
    assert c.http.get("/v1/memories", headers=key).status_code == 401


def test_each_namespace_sees_only_its_own(c: AppClient, t: Tenant) -> None:
    Tenant(c, "globex")
    r = t.submit(t.collection(), "Refunds take 5 days")
    entry = r.json()["entry"]["id"]
    assert c.http.get(f"/admin/v1/ns/globex/kb/entries/{entry}", headers=ADMIN).status_code == 404
    assert get(c, "/ns/globex/kb/entries")["items"] == []
    assert {n["slug"] for n in get(c, "/namespaces")["items"]} == {"acme", "globex"}
    assert c.get(KbEntry, entry).status == KbStatus.ACTIVE


def test_only_configured_origins_may_call_from_a_browser(migrated_url: str) -> None:
    """The Admin may run elsewhere (locally, or its own site) against this backend."""
    preflight = {"Origin": "http://127.0.0.1:8610", "Access-Control-Request-Method": "GET"}
    settings = {"admin_token": TOKEN, "cors_origins": ["http://127.0.0.1:8610"]}
    with running_app(migrated_url, settings=settings) as client:
        r = client.http.options("/admin/v1/namespaces", headers=preflight)
        assert r.headers.get("access-control-allow-origin") == "http://127.0.0.1:8610"
        other = {**preflight, "Origin": "https://evil.example"}
        r = client.http.options("/admin/v1/namespaces", headers=other)
        assert "access-control-allow-origin" not in r.headers
    with running_app(migrated_url, settings={"admin_token": TOKEN}) as client:  # none configured
        r = client.http.options("/admin/v1/namespaces", headers=preflight)
        assert "access-control-allow-origin" not in r.headers
