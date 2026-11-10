"""User tokens (design §7, P5 acceptance: "a user token cannot read another user's data").

A host app's server mints a short-lived token for one end user; a browser holding it reaches only
the routes marked for end users, and only that user's own memories, cards and retrievals. Both
dialects."""

import time
import uuid
from typing import Any

import jwt
import pytest

from llm_steward.models import Candidate, ChangeLog, Episode, Memory
from llm_steward.models.enums import ApiScope, CandidateStatus, EpisodeKind, KbStatus
from llm_steward.services.governance_service import CreateEpisode
from tests.conftest import TEST_TOKEN_SECRET, AppClient, Tenant, post_memory, running_app

H = dict[str, str]


def _token(c: AppClient, key: H, user_id: str, **body: Any) -> H:
    r = c.http.post("/v1/tokens/user", json={"user_id": user_id, **body}, headers=key)
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["user_id"] == user_id and out["token_type"] == "Bearer"
    return {"Authorization": f"Bearer {out['token']}"}


def _bearer(token: str) -> H:
    return {"Authorization": f"Bearer {token}"}


class World:
    """acme: memories for u1, u2 and one shared; u1 holds a user token."""

    def __init__(self, c: AppClient) -> None:
        self.c = c
        self.t = Tenant(c, "acme")
        key = self.t.host
        self.u1 = post_memory(c, key, "u1 likes green tea", user_id="u1")
        self.u2 = post_memory(c, key, "u2 lives in Shanghai", user_id="u2")
        self.shared = post_memory(c, key, "the shop opens at nine")
        c.drain()
        self.token = _token(c, key, "u1")

    def get(self, path: str, **params: Any) -> Any:
        return self.c.http.get(path, params=params, headers=self.token)

    def post(self, path: str, body: Any = None) -> Any:
        return self.c.http.post(path, json=body or {}, headers=self.token)


@pytest.fixture
def w(app_client: AppClient) -> World:
    return World(app_client)


# --- what a token reaches --------------------------------------------------------------------

END_USER_ROUTES = {
    ("POST", "/v1/memories"),
    ("GET", "/v1/memories"),
    ("GET", "/v1/memories/{memory_id}"),
    ("PATCH", "/v1/memories/{memory_id}"),
    ("DELETE", "/v1/memories/{memory_id}"),
    ("GET", "/v1/memories/{memory_id}/history"),
    ("GET", "/v1/profile"),
    ("POST", "/v1/retrieve"),
    ("POST", "/v1/retrieve/{retrieval_id}/feedback"),
    ("GET", "/v1/candidates"),
    ("POST", "/v1/candidates/{candidate_id}/accept"),
    ("POST", "/v1/candidates/{candidate_id}/reject"),
}


def test_only_the_marked_routes_accept_a_user_token(app_client: AppClient) -> None:
    """Default deny for user tokens: a route is open to them only when its `require` names a
    reason, which declares the `UserToken` scheme. Adding one is a reviewed change to this list."""
    spec = app_client.http.app.openapi()  # type: ignore[attr-defined]
    marked = {
        (method.upper(), path)
        for path, operations in spec["paths"].items()
        for method, operation in operations.items()
        if any("UserToken" in requirement for requirement in operation.get("security", []))
    }
    assert marked == END_USER_ROUTES


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/v1/episodes"),
        ("GET", "/v1/kb/entries"),
        ("GET", "/v1/kb/collections"),
        ("POST", "/v1/tokens/user"),  # a token never mints another
        ("DELETE", "/v1/memories"),
        # proposing knowledge: the card would ask the same user, who could then approve their
        # own text into the shared KB (security review of 4204515)
        ("POST", "/v1/suggestions"),
    ],
)
def test_a_user_token_is_refused_elsewhere(w: World, method: str, path: str) -> None:
    r = w.c.http.request(method, path, params={"user_id": "u1"}, json={}, headers=w.token)
    assert r.status_code == 403
    assert "user token" in r.json()["detail"]


# --- one user's own data ---------------------------------------------------------------------


def test_listing_is_pinned_to_the_tokens_user(w: World) -> None:
    r = w.get("/v1/memories")
    assert r.status_code == 200
    assert [m["id"] for m in r.json()["items"]] == [w.u1]
    assert w.get("/v1/memories", user_id="u1").status_code == 200
    assert w.get("/v1/memories", user_id="u2").status_code == 403


def test_another_users_memory_is_not_found_by_id(w: World) -> None:
    assert w.get(f"/v1/memories/{w.u1}").json()["content"] == "u1 likes green tea"
    for memory in (w.u2, w.shared):  # someone else's, or shared: not the user's to see or change
        assert w.get(f"/v1/memories/{memory}").status_code == 404
        assert w.get(f"/v1/memories/{memory}/history").status_code == 404
        r = w.c.http.patch(f"/v1/memories/{memory}", json={"pinned": True}, headers=w.token)
        assert r.status_code == 404
        assert w.c.http.delete(f"/v1/memories/{memory}", headers=w.token).status_code == 404
    assert w.c.get(Memory, w.u2).status.value == "active"  # untouched


def test_a_user_deletes_their_own_memory_as_themselves(w: World) -> None:
    r = w.c.http.delete(f"/v1/memories/{w.u1}", headers=w.token)
    assert r.status_code == 200 and r.json()["status"] == "deleted"
    changes = w.get(f"/v1/memories/{w.u1}/history").json()["changes"]
    assert (changes[-1]["actor_type"], changes[-1]["actor_id"]) == ("end_user", "u1")


def test_a_new_memory_belongs_to_the_tokens_user(w: World) -> None:
    created = w.post("/v1/memories", {"content": "u1 is vegetarian"})
    assert created.status_code == 201 and created.json()["user_id"] == "u1"
    assert w.post("/v1/memories", {"content": "x", "user_id": "u2"}).status_code == 403


def test_profile_and_retrieval_see_only_the_users_own_and_shared(w: World) -> None:
    assert w.get("/v1/profile").json()["user_id"] == "u1"
    assert w.get("/v1/profile", user_id="u2").status_code == 403
    r = w.post("/v1/retrieve", {"query": "tea Shanghai nine", "sources": ["memory"]})
    assert r.status_code == 200
    assert {i["id"] for i in r.json()["items"]} == {w.u1, w.shared}
    r = w.post("/v1/retrieve", {"query": "Shanghai", "scope": {"user_id": "u2"}})
    assert r.status_code == 403


def test_feedback_only_on_the_users_own_retrievals(w: World) -> None:
    theirs = w.c.http.post(
        "/v1/retrieve",
        json={"query": "Shanghai", "scope": {"user_id": "u2"}, "sources": ["memory"]},
        headers=w.t.host,
    ).json()
    url = f"/v1/retrieve/{theirs['retrieval_id']}/feedback"
    assert w.post(url, {"item_id": w.u2, "helpful": True}).status_code == 404
    mine = w.post("/v1/retrieve", {"query": "tea", "sources": ["memory"]}).json()
    url = f"/v1/retrieve/{mine['retrieval_id']}/feedback"
    assert w.post(url, {"item_id": w.u1, "helpful": True}).status_code == 204


def _card(w: World, user_id: str) -> str:
    collection = w.t.collection()
    body = {
        "collection_id": collection,
        "title": f"Note for {user_id}",
        "content": "Gift wrapping is free in December",
        "risk_level": "medium",
        "user_id": user_id,
    }
    r = w.c.http.post("/v1/kb/entries", json=body, headers=w.t.host)
    assert r.status_code == 202, r.text
    return str(r.json()["candidate"]["id"])


def test_a_user_answers_only_their_own_cards_and_never_verifies(w: World) -> None:
    mine, theirs = _card(w, "u1"), _card(w, "u2")
    listed = w.get("/v1/candidates").json()["items"]
    assert [c["id"] for c in listed] == [mine]
    assert w.get("/v1/candidates", user_id="u2").status_code == 403
    assert w.post(f"/v1/candidates/{theirs}/accept").status_code in (403, 404)
    assert w.post(f"/v1/candidates/{theirs}/accept", {"user_id": "u2"}).status_code == 403
    assert w.c.get(Candidate, theirs).status == CandidateStatus.PENDING
    r = w.post(f"/v1/candidates/{mine}/accept")
    assert r.status_code == 200, r.text
    assert r.json()["entry"]["status"] == KbStatus.ACTIVE.value  # an end user never verifies


def test_a_user_cannot_rewrite_a_card_into_shared_knowledge(w: World) -> None:
    """Accepting as proposed, or keeping an edit to themselves, is the user's call; rewriting
    what the team will read is not (security review of 4204515)."""
    card = _card(w, "u1")
    edit = {"draft": {"content": "Gift wrapping is free all year"}}
    r = w.post(f"/v1/candidates/{card}/accept", edit)
    assert r.status_code == 403
    assert w.c.get(Candidate, card).status == CandidateStatus.PENDING
    r = w.post(f"/v1/candidates/{card}/accept", {**edit, "scope": "personal"})
    assert r.status_code == 200, r.text
    # the host app's server may still pass the user's edit on (design §7: it is trusted)
    other = _card(w, "u1")
    r = w.c.http.post(
        f"/v1/candidates/{other}/accept", json={**edit, "user_id": "u1"}, headers=w.t.host
    )
    assert r.status_code == 200, r.text


# --- the token itself ------------------------------------------------------------------------


def _claims(w: World, **overrides: Any) -> dict[str, Any]:
    token = w.token["Authorization"].removeprefix("Bearer ")
    claims = jwt.decode(token, options={"verify_signature": False})
    return {**claims, **overrides}


def _sign(claims: dict[str, Any], secret: str = TEST_TOKEN_SECRET) -> str:
    return jwt.encode(claims, secret, algorithm="HS256")


def test_forged_expired_or_misdirected_tokens_are_401(w: World) -> None:
    now = int(time.time())
    other = Tenant(w.c, "globex")
    other_key_id = str(w.c.call(_key_id, w.c, other.host))
    bad = {
        "tampered": w.token["Authorization"].removeprefix("Bearer ")[:-2] + "xx",
        "wrong secret": _sign(_claims(w), "another-secret-that-is-long-enough-000"),
        "expired": _sign(_claims(w, iat=now - 7200, exp=now - 3600)),
        "wrong audience": _sign(_claims(w, aud="someone-else")),
        "no expiry": _sign({k: v for k, v in _claims(w).items() if k != "exp"}),
        "key of another namespace": _sign(_claims(w, kid=other_key_id)),
        "unknown key": _sign(_claims(w, kid=str(uuid.uuid4()))),
        "alg none": jwt.encode(_claims(w), key=None, algorithm="none"),
    }
    for why, token in bad.items():
        r = w.c.http.get("/v1/memories", headers=_bearer(token))
        assert r.status_code == 401, why
        assert r.headers["WWW-Authenticate"] == "Bearer"


def _key(c: AppClient, slug: str, *scopes: ApiScope) -> H:
    """Another key for an existing namespace."""
    key = c.call(c.services.namespaces.create_key, slug, scopes)
    return {"Authorization": f"Bearer {key}"}


async def _key_id(c: AppClient, headers: H) -> uuid.UUID:
    principal = await c.services.namespaces.authenticate(
        headers["Authorization"].removeprefix("Bearer ")
    )
    assert principal is not None
    return principal.key_id


def test_revoking_the_key_ends_its_tokens(w: World) -> None:
    assert w.get("/v1/memories").status_code == 200
    prefix = w.t.host["Authorization"].split("_")[1]
    w.c.call(w.c.services.namespaces.revoke_key, prefix)
    assert w.get("/v1/memories").status_code == 401


def test_a_token_carries_no_more_than_its_keys_scopes(app_client: AppClient) -> None:
    h, _ = app_client.new_tenant("acme", ApiScope.READ, ApiScope.WRITE, ApiScope.REVIEW)
    token = _token(app_client, h, "u1")
    claims = jwt.decode(
        token["Authorization"].removeprefix("Bearer "), options={"verify_signature": False}
    )
    assert set(claims) >= {"iss", "aud", "sub", "ns", "kid", "iat", "exp", "jti"}
    assert claims["exp"] - claims["iat"] == 900  # 15 minutes by default
    reader = _key(app_client, "acme", ApiScope.READ)
    r = app_client.http.post("/v1/tokens/user", json={"user_id": "u1"}, headers=reader)
    assert r.status_code == 403  # minting needs `write`
    long = app_client.http.post(
        "/v1/tokens/user", json={"user_id": "u1", "ttl_seconds": 3601}, headers=h
    )
    assert long.status_code == 422


def test_a_token_from_a_reviewer_key_cannot_review(app_client: AppClient) -> None:
    """The key's `review` scope does not pass to the end user's token."""
    t = Tenant(app_client, "acme")
    collection = t.collection()
    body = {"collection_id": collection, "title": "Fees", "content": "c", "risk_level": "high"}
    queued = t.post("/v1/kb/entries", body).json()["candidate"]["id"]
    token = _token(app_client, t.reviewer, "u1")
    principal = app_client.call(
        app_client.services.namespaces.authenticate, token["Authorization"].removeprefix("Bearer ")
    )
    assert principal is not None and principal.scopes == {ApiScope.READ, ApiScope.WRITE}
    r = app_client.http.post(f"/v1/candidates/{queued}/accept", json={}, headers=token)
    assert r.status_code == 403
    assert app_client.get(Candidate, queued).status == CandidateStatus.PENDING


def test_without_a_secret_no_token_is_issued_or_accepted(migrated_url: str) -> None:
    with running_app(migrated_url, settings={"token_secret": None}) as c:
        h, ns = c.new_tenant("acme")
        r = c.http.post("/v1/tokens/user", json={"user_id": "u1"}, headers=h)
        assert r.status_code == 503 and r.json()["code"] == "user_tokens_disabled"
        key_id = c.call(_key_id, c, h)
        now = int(time.time())
        claims = {
            "iss": "llm-steward",
            "aud": "llm-steward:user",
            "sub": "u1",
            "ns": str(ns),
            "kid": str(key_id),
            "iat": now,
            "exp": now + 600,
            "jti": "x",
        }
        r = c.http.get("/v1/memories", headers=_bearer(_sign(claims)))
        assert r.status_code == 401


# --- erasure (design §7, rule 6's exception) --------------------------------------------------


def test_erasing_a_user_removes_their_data_everywhere(w: World) -> None:
    c, key = w.c, w.t.host
    said = [{"role": "user", "content": "call me at 13800138000"}]
    episode = c.apply(CreateEpisode(w.t.ns, EpisodeKind.CONVERSATION, said, user_id="u1"))
    kept = c.apply(CreateEpisode(w.t.ns, EpisodeKind.CONVERSATION, said, user_id="u2"))
    card = _card(w, "u1")
    w.post("/v1/retrieve", {"query": "tea"})
    r = c.http.delete("/v1/memories", params={"user_id": "u1"}, headers=key)
    assert r.status_code == 200, r.text
    counts = r.json()
    assert counts["user_id"] == "u1"
    assert (counts["memories"], counts["episodes"], counts["candidates"]) == (1, 1, 1)
    assert counts["retrieval_logs"] >= 1
    assert c.get(Memory, w.u1) is None and c.get(Candidate, card) is None
    assert c.get(Episode, episode.id) is None and c.get(Episode, kept.id) is not None
    assert c.get(Memory, w.u2) is not None and c.get(Memory, w.shared) is not None

    async def leftovers() -> list[str]:
        from sqlalchemy import select

        async with c.sessions() as s:
            gone = [uuid.UUID(w.u1), episode.id, uuid.UUID(card)]
            rows = await s.scalars(select(ChangeLog.object_id).where(ChangeLog.object_id.in_(gone)))
            return [str(r) for r in rows]

    assert c.call(leftovers) == []  # its history went too: the history held its content
    c.drain()
    from tests.integration.test_api import _provider_has

    assert not _provider_has(c, "memory", w.u1)  # the engine's copy is removed by the outbox
    assert _provider_has(c, "memory", w.u2)


def test_erasure_needs_a_user_id_and_a_key(w: World) -> None:
    assert w.c.http.delete("/v1/memories", headers=w.t.host).status_code == 422
    reader = _key(w.c, "acme", ApiScope.READ)
    assert (
        w.c.http.delete("/v1/memories", params={"user_id": "u1"}, headers=reader).status_code == 403
    )
