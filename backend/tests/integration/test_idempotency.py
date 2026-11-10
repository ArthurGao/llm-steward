"""`Idempotency-Key` on writes, per-key rate limiting and job status (design §7, P5b), both
dialects."""

import uuid
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select, update

from llm_steward.errors import ProviderUnavailable
from llm_steward.models import ChangeLog, IdempotencyKey, Job, Memory
from llm_steward.models.base import utcnow
from tests.conftest import AppClient, Tenant, bearer, running_app

H = dict[str, str]


def _post(c: AppClient, h: H, key: str | None, content: str = "likes green tea") -> Any:
    headers = {**h, **({"Idempotency-Key": key} if key else {})}
    return c.http.post("/v1/memories", json={"content": content, "user_id": "u1"}, headers=headers)


# --- Idempotency-Key -------------------------------------------------------------------------


def test_a_retry_with_the_same_key_replays_and_writes_once(app_client: AppClient) -> None:
    h, _ = app_client.new_tenant("acme")
    first = _post(app_client, h, "k-1")
    again = _post(app_client, h, "k-1")
    assert (first.status_code, again.status_code) == (201, 201)
    assert again.json() == first.json()
    assert again.headers["Idempotent-Replayed"] == "true"
    assert "Idempotent-Replayed" not in first.headers
    assert again.headers["content-type"] == first.headers["content-type"]
    assert app_client.count(Memory) == 1
    assert app_client.count(ChangeLog) == 1  # one write, one history row


def test_the_same_key_for_a_different_request_is_refused(app_client: AppClient) -> None:
    h, _ = app_client.new_tenant("acme")
    assert _post(app_client, h, "k-1").status_code == 201
    r = _post(app_client, h, "k-1", content="something else")
    assert r.status_code == 422 and r.json()["code"] == "idempotency_key_reused"
    assert app_client.count(Memory) == 1


def test_a_key_still_in_flight_is_a_conflict(app_client: AppClient) -> None:
    h, ns = app_client.new_tenant("acme")
    principal = app_client.call(app_client.services.namespaces.authenticate, bearer(h))
    assert principal is not None
    from llm_steward.services.idempotency_service import principal_ref, request_hash

    body = b'{"content": "likes green tea", "user_id": "u1"}'
    digest = request_hash("POST", "/v1/memories", "", "application/json", body)
    app_client.call(
        app_client.services.idempotency.reserve, ns, principal_ref(principal), "k-1", digest
    )
    headers = {**h, "Idempotency-Key": "k-1", "content-type": "application/json"}
    r = app_client.http.post("/v1/memories", content=body, headers=headers)  # the same bytes
    assert r.status_code == 409 and r.json()["code"] == "idempotency_in_progress"
    assert app_client.count(Memory) == 0


def test_a_server_error_releases_the_key_so_a_retry_runs(
    app_client: AppClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    h, _ = app_client.new_tenant("acme")
    memories = app_client.services.memories
    create = memories.create

    async def down(*args: Any, **kwargs: Any) -> Any:
        raise ProviderUnavailable("engine down")

    monkeypatch.setattr(memories, "create", down)
    assert _post(app_client, h, "k-1").status_code == 503
    assert app_client.count(IdempotencyKey) == 0  # released
    monkeypatch.setattr(memories, "create", create)
    r = _post(app_client, h, "k-1")
    assert r.status_code == 201 and "Idempotent-Replayed" not in r.headers
    assert _post(app_client, h, "k-1").headers["Idempotent-Replayed"] == "true"


def test_a_rate_limited_attempt_is_not_the_answer(
    app_client: AppClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """429 says "try again", like a server error: the key is released, not replayed."""
    h, _ = app_client.new_tenant("acme")
    limiter = app_client.services.rate_limiter
    waits = [5.0]

    async def busy_once(*args: Any) -> float | None:
        return waits.pop() if waits else None

    monkeypatch.setattr(limiter, "check", busy_once)
    assert _post(app_client, h, "k-1").status_code == 429
    assert app_client.count(IdempotencyKey) == 0  # refused before anything was reserved
    r = _post(app_client, h, "k-1")
    assert r.status_code == 201 and "Idempotent-Replayed" not in r.headers


def test_two_users_tokens_from_one_key_have_their_own_keys(app_client: AppClient) -> None:
    h, _ = app_client.new_tenant("acme")
    tokens = [app_client.user_token(h, user) for user in ("u1", "u2")]
    body = {"content": "likes green tea"}
    first, second = (
        app_client.http.post("/v1/memories", json=body, headers={**t, "Idempotency-Key": "k-1"})
        for t in tokens
    )
    assert (first.status_code, second.status_code) == (201, 201)
    assert "Idempotent-Replayed" not in second.headers
    assert (first.json()["user_id"], second.json()["user_id"]) == ("u1", "u2")


def test_a_try_again_answer_from_the_route_is_not_kept(
    app_client: AppClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A route that itself answers 429 (or 408) said "try again": the key is released too."""
    from llm_steward.errors import StewardError

    class Busy(StewardError):
        status, code = 429, "busy"

    h, _ = app_client.new_tenant("acme")
    memories = app_client.services.memories
    create, answers = memories.create, [Busy("engine busy")]

    async def busy_once(*args: Any, **kwargs: Any) -> Any:
        if answers:
            raise answers.pop()
        return await create(*args, **kwargs)

    monkeypatch.setattr(memories, "create", busy_once)
    assert _post(app_client, h, "k-1").status_code == 429
    r = _post(app_client, h, "k-1")
    assert r.status_code == 201 and "Idempotent-Replayed" not in r.headers


def test_the_caller_is_authenticated_once_per_request(
    app_client: AppClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The middleware and the route share one authentication (and one rate-limit charge)."""
    h, _ = app_client.new_tenant("acme")
    namespaces = app_client.services.namespaces
    authenticate, calls = namespaces.authenticate, []

    async def counted(token: str) -> Any:
        calls.append(token)
        return await authenticate(token)

    monkeypatch.setattr(namespaces, "authenticate", counted)
    assert _post(app_client, h, "k-1").status_code == 201
    assert len(calls) == 1


def test_an_oversized_body_is_refused_before_it_is_buffered(migrated_url: str) -> None:
    with running_app(migrated_url, settings={"kb_max_upload_mb": 1}) as c:
        h, _ = c.new_tenant("acme")
        headers = {**h, "Idempotency-Key": "k-big", "content-type": "application/json"}
        r = c.http.post("/v1/memories", content=b"x" * (2 * 2**20 + 1), headers=headers)
        assert r.status_code == 413 and r.json()["code"] == "too_large"
        assert c.count(IdempotencyKey) == 0


def test_a_client_error_is_replayed_too(app_client: AppClient) -> None:
    """A 4xx is the answer to that request: the same retry gets the same answer."""
    h, _ = app_client.new_tenant("acme")
    url = f"/v1/memories/{uuid.uuid4()}"
    headers = {**h, "Idempotency-Key": "k-404"}
    first = app_client.http.delete(url, headers=headers)
    again = app_client.http.delete(url, headers=headers)
    assert (first.status_code, again.status_code) == (404, 404)
    assert again.headers["Idempotent-Replayed"] == "true"


def test_keys_belong_to_the_caller(app_client: AppClient) -> None:
    t = Tenant(app_client, "acme")
    other, _ = app_client.new_tenant("globex")
    assert _post(app_client, t.host, "k-1").status_code == 201
    r = _post(app_client, other, "k-1")  # another namespace: its own key space
    assert r.status_code == 201 and "Idempotent-Replayed" not in r.headers
    r = _post(app_client, t.reviewer, "k-1")  # another key of the same namespace, too
    assert r.status_code == 201 and "Idempotent-Replayed" not in r.headers
    assert app_client.count(Memory) == 3


def test_without_the_header_nothing_is_stored(app_client: AppClient) -> None:
    h, _ = app_client.new_tenant("acme")
    assert _post(app_client, h, None).status_code == 201
    assert _post(app_client, h, None).status_code == 201
    assert app_client.count(Memory) == 2 and app_client.count(IdempotencyKey) == 0


@pytest.mark.parametrize("key", ["", "x" * 201, "bad\u0007key"])
def test_a_malformed_key_is_refused(app_client: AppClient, key: str) -> None:
    h, _ = app_client.new_tenant("acme")
    headers = {**h, "Idempotency-Key": key}
    r = app_client.http.post("/v1/memories", json={"content": "c"}, headers=headers)
    assert r.status_code == 400 and r.json()["code"] == "invalid_idempotency_key"
    assert app_client.count(Memory) == 0


def test_credentials_and_reads_are_never_stored(app_client: AppClient) -> None:
    """A minted token would sit in the table as a live bearer credential, and a retrieval's
    answer is memory content: neither needs replaying (minting twice is harmless, reads write
    nothing), so the header is ignored there (security review of e98164f)."""
    h, _ = app_client.new_tenant("acme")
    headers = {**h, "Idempotency-Key": "k-1"}
    first, again = (
        app_client.http.post("/v1/tokens/user", json={"user_id": "u1"}, headers=headers)
        for _ in range(2)
    )
    assert (first.status_code, again.status_code) == (201, 201)
    assert first.json()["token"] != again.json()["token"]  # minted twice, nothing replayed
    r = app_client.http.post("/v1/retrieve", json={"query": "tea"}, headers=headers)
    assert r.status_code == 200 and "Idempotent-Replayed" not in r.headers
    assert app_client.count(IdempotencyKey) == 0


def test_erasing_a_user_forgets_stored_responses(app_client: AppClient) -> None:
    """A stored response can carry the user's content and an API key's write cannot be tied to
    the user it was for: erasure clears the namespace's (a day's retry cache, nothing more)."""
    h, _ = app_client.new_tenant("acme")
    other, _ = app_client.new_tenant("globex")
    assert _post(app_client, h, "k-1").status_code == 201
    assert _post(app_client, other, "k-1").status_code == 201
    r = app_client.http.delete("/v1/memories", params={"user_id": "u1"}, headers=h)
    assert r.status_code == 200
    assert app_client.count(IdempotencyKey) == 1  # only the other namespace's is left


def test_after_a_day_a_key_is_forgotten(app_client: AppClient) -> None:
    h, _ = app_client.new_tenant("acme")
    assert _post(app_client, h, "k-1").status_code == 201

    async def age() -> None:
        async with app_client.sessions() as s, s.begin():
            await s.execute(
                update(IdempotencyKey).values(created_at=utcnow() - timedelta(hours=25))
            )

    app_client.call(age)
    r = _post(app_client, h, "k-1")  # stale: a new request
    assert r.status_code == 201 and "Idempotent-Replayed" not in r.headers
    app_client.call(age)
    assert app_client.call(app_client.services.idempotency.purge) == 1
    assert app_client.count(IdempotencyKey) == 0


# --- rate limiting ---------------------------------------------------------------------------


def test_a_key_over_its_limit_gets_429_with_retry_after(migrated_url: str) -> None:
    with running_app(migrated_url, settings={"rate_limit_per_minute": 3}) as c:
        h, _ = c.new_tenant("acme")
        other, _ = c.new_tenant("globex")
        assert [c.http.get("/v1/memories", headers=h).status_code for _ in range(3)] == [200] * 3
        r = c.http.get("/v1/memories", headers=h)
        assert r.status_code == 429 and r.json()["code"] == "rate_limited"
        assert r.headers["Retry-After"] == "20"
        assert c.http.get("/v1/memories", headers=other).status_code == 200  # its own bucket


def test_a_user_token_spends_its_issuing_keys_budget(migrated_url: str) -> None:
    with running_app(migrated_url, settings={"rate_limit_per_minute": 3}) as c:
        h, _ = c.new_tenant("acme")
        r = c.http.post("/v1/tokens/user", json={"user_id": "u1"}, headers=h)  # 1
        token = {"Authorization": f"Bearer {r.json()['token']}"}
        assert c.http.get("/v1/memories", headers=token).status_code == 200  # 2
        assert c.http.get("/v1/memories", headers=h).status_code == 200  # 3
        assert c.http.get("/v1/memories", headers=token).status_code == 429


def test_a_namespace_can_set_its_own_limit(migrated_url: str) -> None:
    with running_app(migrated_url, settings={"rate_limit_per_minute": 100}) as c:
        h, ns = c.new_tenant("acme")
        c.set_config(ns, {"limits": {"requests_per_minute": 1}})
        assert c.http.get("/v1/memories", headers=h).status_code == 200
        assert c.http.get("/v1/memories", headers=h).status_code == 429


def test_unauthenticated_requests_are_not_counted_against_anyone(migrated_url: str) -> None:
    with running_app(migrated_url, settings={"rate_limit_per_minute": 1}) as c:
        h, _ = c.new_tenant("acme")
        for _ in range(5):
            assert c.http.get("/v1/memories").status_code == 401
        assert c.http.get("/v1/memories", headers=h).status_code == 200


# --- job status ------------------------------------------------------------------------------


def _a_job(c: AppClient, ns: uuid.UUID) -> uuid.UUID:
    async def read() -> uuid.UUID:
        async with c.sessions() as s:
            job = await s.scalar(select(Job.id).where(Job.namespace_id == ns).limit(1))
            assert job is not None
            return job

    return c.call(read)


def test_a_job_is_visible_to_its_namespace_only(app_client: AppClient) -> None:
    t = Tenant(app_client, "acme")
    _post(app_client, t.host, None)  # queues the push to the memory engine
    job_id = _a_job(app_client, t.ns)
    r = app_client.http.get(f"/v1/jobs/{job_id}", headers=t.host)
    assert r.status_code == 200
    body = r.json()
    assert (body["id"], body["type"], body["status"]) == (
        str(job_id),
        "sync.memory_upsert",
        "pending",
    )
    assert body["attempts"] == 0 and body["error"] is None
    assert "payload" not in body
    other, _ = app_client.new_tenant("globex")
    assert app_client.http.get(f"/v1/jobs/{job_id}", headers=other).status_code == 404
    assert app_client.http.get(f"/v1/jobs/{uuid.uuid4()}", headers=t.host).status_code == 404
    app_client.drain()
    assert app_client.http.get(f"/v1/jobs/{job_id}", headers=t.host).json()["status"] == "done"
