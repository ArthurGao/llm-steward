"""The KB and candidate API (design §5, §7; P3 acceptance), end to end through the app, the
worker and the native KB provider, on both dialects."""

import uuid
from typing import Any

from tests.conftest import Tenant


async def _set_config(engine: Any, namespace_id: uuid.UUID, config: dict[str, Any]) -> None:
    """A namespace's config (design §8) — no API sets it yet (`steward ns apply` comes later)."""
    from llm_steward.models import Namespace
    from llm_steward.storage.db import session_factory

    async with session_factory(engine)() as s, s.begin():
        namespace = await s.get(Namespace, namespace_id)
        assert namespace is not None
        namespace.config = config


def test_low_risk_knowledge_goes_live(t: Tenant) -> None:
    r = t.submit(t.collection(default_risk="low"), "Refunds take 3 to 5 business days")
    assert r.status_code == 201 and r.json()["entry"]["status"] == "active"
    assert r.json()["candidate"] is None
    t.c.drain()
    assert t.retrieve() == [r.json()["entry"]["id"]]


def test_high_risk_never_reaches_the_provider_before_a_reviewer_approves(t: Tenant) -> None:
    collection = t.collection(default_risk="low")
    r = t.submit(collection, "Refunds take 3 to 5 business days", risk_level="high")
    assert r.status_code == 202
    candidate = r.json()["candidate"]
    assert (candidate["stage"], candidate["status"]) == ("awaiting_review", "pending")
    t.c.drain()
    assert t.retrieve() == []

    refused = t.post(f"/v1/candidates/{candidate['id']}/accept")  # the host app is no reviewer
    assert refused.status_code == 403
    approved = t.post(f"/v1/candidates/{candidate['id']}/accept", as_reviewer=True)
    assert approved.status_code == 200, approved.text
    entry = approved.json()["entry"]
    assert entry["status"] == "verified"
    t.c.drain()
    assert t.retrieve() == [entry["id"]]


def test_an_end_users_confirmation_never_yields_verified(t: Tenant) -> None:
    collection = t.collection(default_risk="medium")
    r = t.submit(collection, "Refunds take 3 to 5 business days", user_id="alice")
    assert r.status_code == 202
    card = r.json()["candidate"]
    assert (card["stage"], card["requested_for_user"]) == ("awaiting_user", "alice")

    listed = t.c.http.get("/v1/candidates?stage=awaiting_user&user_id=alice", headers=t.host)
    assert [c["id"] for c in listed.json()["items"]] == [card["id"]]

    bob = t.post(f"/v1/candidates/{card['id']}/accept", {"user_id": "bob"})
    assert bob.status_code == 403
    accepted = t.post(f"/v1/candidates/{card['id']}/accept", {"user_id": "alice", "scope": "team"})
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["entry"]["status"] == "active"  # useful, not verified


def test_only_a_reviewer_verifies_or_deprecates(t: Tenant) -> None:
    entry = t.submit(t.collection(default_risk="low"), "Refunds take 3 days").json()["entry"]
    for action in ("verify", "deprecate"):
        assert t.post(f"/v1/kb/entries/{entry['id']}/{action}").status_code == 403
    verified = t.post(f"/v1/kb/entries/{entry['id']}/verify", as_reviewer=True)
    assert verified.status_code == 200 and verified.json()["status"] == "verified"
    assert verified.json()["verified_by"].startswith("api_key:")


def test_a_deprecated_entry_is_gone_from_retrieval_before_the_worker_runs(t: Tenant) -> None:
    entry = t.submit(t.collection(default_risk="low"), "Refunds take 3 days").json()["entry"]
    t.c.drain()
    assert t.retrieve() == [entry["id"]]
    deprecated = t.post(f"/v1/kb/entries/{entry['id']}/deprecate", as_reviewer=True)
    assert deprecated.status_code == 200 and deprecated.json()["status"] == "deprecated"
    # worker NOT drained: the provider still has it, retrieval does not return it
    assert t.in_provider(entry)
    assert t.retrieve() == []


def test_personal_data_is_redacted_and_sent_to_review(t: Tenant) -> None:
    r = t.submit(t.collection(default_risk="low"), "Write to alice@example.com for refunds")
    assert r.status_code == 202
    candidate = r.json()["candidate"]
    assert candidate["draft"]["content"] == "Write to [REDACTED:email] for refunds"
    assert candidate["risk_level"] == "high" and "pii: email" in candidate["reason"]


def test_a_namespace_keyword_marks_knowledge_high_risk(t: Tenant) -> None:
    t.c.call(_set_config, t.c.services.engine, t.ns, {"kb": {"high_risk_keywords": ["法律"]}})
    r = t.submit(t.collection(default_risk="low"), "本条款受法律约束")
    assert r.status_code == 202 and r.json()["candidate"]["stage"] == "awaiting_review"


def test_editing_creates_a_new_version(t: Tenant) -> None:
    entry = t.submit(t.collection(default_risk="low"), "Refunds take 3 days").json()["entry"]
    r = t.c.http.patch(
        f"/v1/kb/entries/{entry['id']}", json={"content": "Refunds take 4 days"}, headers=t.host
    )
    assert r.status_code == 200 and r.json()["entry"]["version"] == 2
    got = t.c.http.get(f"/v1/kb/entries/{entry['id']}", headers=t.host).json()
    assert (got["content"], got["version"]) == ("Refunds take 4 days", 2)


def test_collections_are_managed_by_reviewers_and_policies_are_checked(t: Tenant) -> None:
    body = {"name": "faq"}
    assert t.post("/v1/kb/collections", body).status_code == 403
    collection = t.collection(review_policy={"low": "review"})
    bad = t.c.http.patch(
        f"/v1/kb/collections/{collection}",
        json={"review_policy": {"high": "auto"}},
        headers=t.reviewer,
    )
    assert bad.status_code == 422
    listed = t.c.http.get("/v1/kb/collections", headers=t.host).json()["items"]
    assert [(c["id"], c["review_policy"]) for c in listed] == [(collection, {"low": "review"})]


def test_another_namespace_sees_nothing(t: Tenant) -> None:
    entry = t.submit(t.collection(default_risk="low"), "Refunds take 3 days").json()["entry"]
    card = t.submit(t.collection(), "x", risk_level="high").json()["candidate"]
    other = Tenant(t.c, "other")
    assert t.c.http.get(f"/v1/kb/entries/{entry['id']}", headers=other.host).status_code == 404
    assert other.post(f"/v1/candidates/{card['id']}/accept", as_reviewer=True).status_code == 404
    assert t.c.http.get("/v1/candidates", headers=other.host).json()["items"] == []


def test_an_edited_draft_is_screened_again_before_it_goes_live(t: Tenant) -> None:
    card = t.submit(t.collection(default_risk="medium"), "Refunds take 3 days", user_id="alice")
    card = card.json()["candidate"]
    edit = {"content": "Refunds: write to alice@example.com"}
    r = t.post(f"/v1/candidates/{card['id']}/accept", {"user_id": "alice", "draft": edit})
    assert r.status_code == 200, r.text
    moved = r.json()
    assert (moved["status"], moved["stage"], moved["risk_level"]) == (
        "pending",
        "awaiting_review",  # personal data made it high: a reviewer decides, nothing went live
        "high",
    )
    assert moved["draft"]["content"] == "Refunds: write to [REDACTED:email]"
    assert moved["entry"] is None


def test_tags_are_screened_and_bounded(t: Tenant) -> None:
    collection = t.collection(default_risk="low")
    r = t.submit(collection, "Refunds take 3 days", tags=["billing", "alice@example.com"])
    assert r.status_code == 202 and r.json()["candidate"]["draft"]["tags"] == [
        "billing",
        "[REDACTED:email]",
    ]
    assert t.submit(collection, "x", tags=["t" * 101]).status_code == 422
