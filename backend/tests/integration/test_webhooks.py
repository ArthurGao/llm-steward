"""Webhooks (design §7, P5c): queued with the governed write, delivered by the outbox worker,
signed with HMAC-SHA256, carrying ids and states only. Both dialects; the receiver is a fake
network at the boundary."""

import hashlib
import hmac
import json
import uuid
from typing import Any

import httpx2
import pytest

from llm_steward.models.enums import JobStatus, JobType, KbStatus
from llm_steward.services.governance_service import TransitionKbEntry
from tests.conftest import AppClient, Tenant, fake_network, kb_entry_op

SECRET = "whsec-test-0123456789"
HOOK = "https://hooks.example.com/steward"


class Receiver:
    """The host app's endpoint: records what arrives, answers what the test says."""

    def __init__(self) -> None:
        self.got: list[httpx2.Request] = []
        self.answers: list[int] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.got.append(request)
        return httpx2.Response(self.answers.pop(0) if self.answers else 204)

    def events(self) -> list[dict[str, Any]]:
        return [json.loads(r.content) for r in self.got]


@pytest.fixture
def hooked(app_client: AppClient, monkeypatch: pytest.MonkeyPatch) -> tuple[Tenant, Receiver]:
    monkeypatch.setenv("STEWARD_WEBHOOK_SECRET_ACME", SECRET)
    receiver = Receiver()
    fake_network(app_client, monkeypatch, receiver)
    t = Tenant(app_client, "acme")
    hook = {
        "url": HOOK,
        "events": ["candidate.created", "kb_entry.verified", "kb_entry.deprecated"],
    }
    app_client.set_config(
        t.ns, {"webhooks": [{**hook, "secret_env": "STEWARD_WEBHOOK_SECRET_ACME"}]}
    )
    return t, receiver


def _high_risk_entry(t: Tenant, collection: str) -> dict[str, Any]:
    body = {
        "collection_id": collection,
        "title": "Refund law",
        "content": "c",
        "risk_level": "high",
    }
    r = t.post("/v1/kb/entries", body)
    assert r.status_code == 202, r.text
    return dict(r.json())


def test_a_review_request_reaches_the_host_signed(hooked: tuple[Tenant, Receiver]) -> None:
    t, receiver = hooked
    collection = t.collection()
    candidate = _high_risk_entry(t, collection)["candidate"]
    assert receiver.got == []  # never from the request: the outbox delivers
    t.c.drain()
    [request] = receiver.got
    assert str(request.url) == "https://93.184.216.34/steward"  # pinned to the checked address
    assert request.headers["host"] == "hooks.example.com"
    [event] = receiver.events()
    assert event["type"] == "candidate.created"
    assert event["data"]["candidate_id"] == candidate["id"]
    assert event["data"]["stage"] == "awaiting_review"
    assert request.headers["Steward-Event-Id"] == event["id"]
    stamp, signature = (p.split("=", 1)[1] for p in request.headers["Steward-Signature"].split(","))
    expected = hmac.new(SECRET.encode(), f"{stamp}.".encode() + request.content, hashlib.sha256)
    assert hmac.compare_digest(signature, expected.hexdigest())


def test_an_event_carries_ids_and_states_never_content(hooked: tuple[Tenant, Receiver]) -> None:
    t, receiver = hooked
    _high_risk_entry(t, t.collection())
    t.c.drain()
    body = receiver.got[0].content.decode()
    assert "Refund law" not in body and '"content"' not in body and '"title"' not in body


def test_verified_and_deprecated_knowledge_is_announced(hooked: tuple[Tenant, Receiver]) -> None:
    t, receiver = hooked
    entry = t.c.apply(kb_entry_op(t.ns, uuid.UUID(t.collection()), KbStatus.ACTIVE))
    t.c.apply(TransitionKbEntry(t.ns, entry.id, KbStatus.VERIFIED))
    t.c.apply(TransitionKbEntry(t.ns, entry.id, KbStatus.DEPRECATED))
    t.c.drain()
    events = [(e["type"], e["data"]["entry_id"], e["data"]["status"]) for e in receiver.events()]
    assert events == [
        ("kb_entry.verified", str(entry.id), "verified"),
        ("kb_entry.deprecated", str(entry.id), "deprecated"),
    ]


def test_only_subscribed_events_are_sent(hooked: tuple[Tenant, Receiver]) -> None:
    t, receiver = hooked
    hook = {
        "url": HOOK,
        "events": ["kb_entry.deprecated"],
        "secret_env": "STEWARD_WEBHOOK_SECRET_ACME",
    }
    t.c.set_config(t.ns, {"webhooks": [hook]})
    _high_risk_entry(t, t.collection())  # candidate.created: not subscribed
    t.c.drain()
    assert receiver.got == [] and t.c.jobs(JobType.WEBHOOK_DELIVER) == []


def test_a_parsed_document_is_announced(hooked: tuple[Tenant, Receiver]) -> None:
    t, receiver = hooked
    hook = {"url": HOOK, "events": ["document.ready"], "secret_env": "STEWARD_WEBHOOK_SECRET_ACME"}
    t.c.set_config(t.ns, {"webhooks": [hook]})
    collection = t.collection(default_risk="low")
    uploaded = t.upload(collection, b"# Refunds\n\nRefunds take 5 days.")
    assert uploaded.status_code == 202, uploaded.text
    t.c.settle(1)
    [event] = receiver.events()
    assert event["type"] == "document.ready"
    document = uploaded.json()["document"]
    assert event["data"] == {
        "document_id": document["id"],
        "collection_id": collection,
        "status": "active",
    }


@pytest.mark.parametrize(
    ("answers", "status", "tries"),
    [
        ([503, 204], JobStatus.DONE, 2),  # a server error: retried
        ([429, 204], JobStatus.DONE, 2),  # throttled: retried
        ([410], JobStatus.FAILED, 1),  # the endpoint refuses: retrying cannot help
        ([301], JobStatus.FAILED, 1),  # redirects are not followed for webhooks
    ],
)
def test_delivery_retries_only_what_retrying_can_fix(
    hooked: tuple[Tenant, Receiver], answers: list[int], status: JobStatus, tries: int
) -> None:
    t, receiver = hooked
    receiver.answers = list(answers)
    _high_risk_entry(t, t.collection())
    t.c.settle(1)
    [job] = t.c.jobs(JobType.WEBHOOK_DELIVER)
    assert (job.status, len(receiver.got)) == (status, tries)


def test_a_missing_secret_fails_the_delivery_without_sending(
    hooked: tuple[Tenant, Receiver], monkeypatch: pytest.MonkeyPatch
) -> None:
    t, receiver = hooked
    monkeypatch.delenv("STEWARD_WEBHOOK_SECRET_ACME")
    _high_risk_entry(t, t.collection())
    t.c.drain()
    [job] = t.c.jobs(JobType.WEBHOOK_DELIVER)
    assert job.status == JobStatus.FAILED and "STEWARD_WEBHOOK_SECRET_ACME" in (job.error or "")
    assert receiver.got == []  # never sent unsigned


def test_an_internal_address_is_never_called(
    hooked: tuple[Tenant, Receiver], monkeypatch: pytest.MonkeyPatch
) -> None:
    t, receiver = hooked

    async def internal_dns(host: str, port: int) -> list[str]:
        return ["169.254.169.254"]

    monkeypatch.setattr(t.c.services.outbound, "resolve", internal_dns)
    _high_risk_entry(t, t.collection())
    t.c.drain()
    [job] = t.c.jobs(JobType.WEBHOOK_DELIVER)
    assert job.status == JobStatus.FAILED and receiver.got == []


def test_a_removed_webhook_is_not_called_for_queued_events(
    hooked: tuple[Tenant, Receiver],
) -> None:
    t, receiver = hooked
    _high_risk_entry(t, t.collection())
    t.c.set_config(t.ns, {})  # the host unsubscribed before the worker ran
    t.c.drain()
    [job] = t.c.jobs(JobType.WEBHOOK_DELIVER)
    assert job.status == JobStatus.DONE and receiver.got == []
