"""Importing a document from a URL (design §7 `POST /kb/documents` with a URL; P5c2): the request
only records it; the worker fetches through the outbound guard (no SSRF), then the document
follows the upload path. Both dialects; the web is a fake network at the boundary."""

from typing import Any

import httpx2
import pytest

from llm_steward.models import KbDocument, KbDocumentFile
from llm_steward.models.enums import DocStatus, JobStatus, JobType
from tests.conftest import AppClient, Tenant, public_dns

PAGE = b"# Refunds\n\nRefunds take five business days."


class Web:
    """The internet as the test sees it: path -> answers (the last one repeats)."""

    def __init__(self) -> None:
        self.routes: dict[str, list[httpx2.Response]] = {}
        self.calls: list[str] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        key = request.headers["host"] + request.url.path
        self.calls.append(key)
        answers = self.routes.get(key) or [httpx2.Response(404)]
        return answers.pop(0) if len(answers) > 1 else answers[0]


@pytest.fixture
def web(app_client: AppClient, monkeypatch: pytest.MonkeyPatch) -> Web:
    fake = Web()
    documents = app_client.services.documents
    monkeypatch.setattr(documents, "transport", httpx2.MockTransport(fake))
    monkeypatch.setattr(documents, "resolve", public_dns)
    return fake


def _import(t: Tenant, collection: str, url: str, **form: str) -> Any:
    data = {"collection_id": collection, "url": url, **form}
    return t.c.http.post("/v1/kb/documents", data=data, headers=t.host)


def _doc(t: Tenant, document_id: str) -> KbDocument:
    doc: KbDocument = t.c.get(KbDocument, document_id)
    return doc


def _run(t: Tenant) -> None:
    for _ in range(3):  # fetch, ingest, parse-status polls
        t.c.drain()
        t.c.make_jobs_due()
    t.c.drain()


@pytest.fixture
def t(app_client: AppClient) -> Tenant:
    return Tenant(app_client, "acme")


def test_a_url_is_fetched_by_the_worker_then_ingested(t: Tenant, web: Web) -> None:
    web.routes["docs.example.com/policies/refunds.md"] = [httpx2.Response(200, content=PAGE)]
    collection = t.collection(default_risk="low")
    r = _import(t, collection, "https://docs.example.com/policies/refunds.md")
    assert r.status_code == 202, r.text
    doc = r.json()["document"]
    assert (doc["state"], doc["status"], doc["filename"]) == ("active", "pending", "refunds.md")
    assert web.calls == []  # never fetched on the request path
    assert [j.type for j in t.c.jobs(JobType.DOC_FETCH)] == [JobType.DOC_FETCH]
    assert t.c.jobs(JobType.DOC_INGEST) == []  # nothing to hand over before the fetch
    _run(t)
    stored = _doc(t, doc["id"])
    assert stored.source_uri == "https://docs.example.com/policies/refunds.md"
    assert (stored.status, stored.size_bytes) == (DocStatus.READY, len(PAGE))
    assert t.c.get(KbDocumentFile, doc["id"]) is None  # handed over, the bytes are gone
    assert web.calls == ["docs.example.com/policies/refunds.md"]


def test_a_name_without_a_type_takes_it_from_the_answer(t: Tenant, web: Web) -> None:
    answer = httpx2.Response(200, content=PAGE, headers={"content-type": "text/markdown"})
    web.routes["docs.example.com/faq"] = [answer]
    r = _import(t, t.collection(default_risk="low"), "https://docs.example.com/faq")
    _run(t)
    doc = _doc(t, r.json()["document"]["id"])
    assert (doc.filename, doc.status) == ("faq.md", DocStatus.READY)


@pytest.mark.parametrize(
    ("setup", "url", "error"),
    [
        ("internal", "https://intranet.example.com/a.md", "not a public https address"),
        ("redirect", "https://docs.example.com/moved.md", "not a public https address"),
        ("big", "https://docs.example.com/big.md", "too large"),
        ("missing", "https://docs.example.com/gone.md", "HTTP 404"),
        ("image", "https://docs.example.com/picture", "unsupported document type"),
        ("loop", "https://docs.example.com/loop.md", "too many redirects"),
    ],
)
def test_a_fetch_that_cannot_succeed_fails_the_document(
    t: Tenant, web: Web, monkeypatch: pytest.MonkeyPatch, setup: str, url: str, error: str
) -> None:
    if setup == "internal":

        async def internal(host: str, port: int) -> list[str]:
            return ["10.0.0.5"]

        monkeypatch.setattr(t.c.services.documents, "resolve", internal)
    web.routes["docs.example.com/moved.md"] = [
        httpx2.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data"})
    ]
    web.routes["docs.example.com/loop.md"] = [
        httpx2.Response(302, headers={"location": "/loop.md"})
    ]
    web.routes["docs.example.com/big.md"] = [httpx2.Response(200, content=b"x" * (2**20 + 1))]
    web.routes["docs.example.com/picture"] = [
        httpx2.Response(200, content=b"\x89PNG", headers={"content-type": "image/png"})
    ]
    monkeypatch.setattr(t.c.services.documents, "max_bytes", 2**20)
    r = _import(t, t.collection(default_risk="low"), url)
    assert r.status_code == 202, r.text
    _run(t)
    doc = _doc(t, r.json()["document"]["id"])
    assert doc.status == DocStatus.FAILED
    assert error in (doc.error or "")
    assert "example.com" not in (doc.error or "")  # a fixed text, never the URL
    assert t.c.get(KbDocumentFile, doc.id) is None


def test_a_busy_server_is_retried(t: Tenant, web: Web) -> None:
    web.routes["docs.example.com/a.md"] = [httpx2.Response(503), httpx2.Response(200, content=PAGE)]
    r = _import(t, t.collection(default_risk="low"), "https://docs.example.com/a.md")
    _run(t)
    assert _doc(t, r.json()["document"]["id"]).status == DocStatus.READY
    assert len(web.calls) == 2


def test_a_server_busy_to_the_end_fails_the_document_and_the_job(t: Tenant, web: Web) -> None:
    web.routes["docs.example.com/a.md"] = [httpx2.Response(503)]
    r = _import(t, t.collection(default_risk="low"), "https://docs.example.com/a.md")
    for _ in range(10):  # more passes than the job has attempts
        t.c.make_jobs_due()
        t.c.drain()
    doc = _doc(t, r.json()["document"]["id"])
    assert (doc.status, doc.error) == (DocStatus.FAILED, "fetch failed: HTTP 503")
    [job] = t.c.jobs(JobType.DOC_FETCH)
    assert (job.status, job.attempts) == (JobStatus.FAILED, job.max_attempts)  # visible
    assert len(web.calls) == job.max_attempts


def test_a_namespace_allowlist_limits_the_hosts(t: Tenant, web: Web) -> None:
    t.c.set_config(t.ns, {"documents": {"url_allowlist": ["wiki.example.org"]}})
    web.routes["docs.example.com/a.md"] = [httpx2.Response(200, content=PAGE)]
    r = _import(t, t.collection(default_risk="low"), "https://docs.example.com/a.md")
    _run(t)
    doc = _doc(t, r.json()["document"]["id"])
    assert doc.status == DocStatus.FAILED and "allowlist" in (doc.error or "")
    assert web.calls == []


def test_a_document_awaiting_review_is_fetched_but_waits(t: Tenant, web: Web) -> None:
    web.routes["docs.example.com/law.md"] = [httpx2.Response(200, content=PAGE)]
    r = _import(t, t.collection(default_risk="high"), "https://docs.example.com/law.md")
    assert r.status_code == 202
    doc, candidate = r.json()["document"], r.json()["candidate"]
    assert doc["state"] == "draft" and candidate is not None
    _run(t)
    assert _doc(t, doc["id"]).status == DocStatus.PENDING  # fetched, not handed to the engine
    assert t.c.get(KbDocumentFile, doc["id"]) is not None
    accepted = t.c.http.post(
        f"/v1/candidates/{candidate['id']}/accept", json={}, headers=t.reviewer
    )
    assert accepted.status_code == 200, accepted.text
    _run(t)
    assert _doc(t, doc["id"]).status == DocStatus.READY


def test_approved_before_the_fetch_finishes_it_is_ingested_after(t: Tenant, web: Web) -> None:
    web.routes["docs.example.com/law.md"] = [httpx2.Response(200, content=PAGE)]
    r = _import(t, t.collection(default_risk="high"), "https://docs.example.com/law.md")
    doc, candidate = r.json()["document"], r.json()["candidate"]
    accepted = t.c.http.post(
        f"/v1/candidates/{candidate['id']}/accept", json={}, headers=t.reviewer
    )
    assert accepted.status_code == 200
    assert t.c.jobs(JobType.DOC_INGEST) == []  # nothing to hand over yet: the fetch queues it
    _run(t)
    assert _doc(t, doc["id"]).status == DocStatus.READY


def test_the_same_content_twice_is_a_duplicate(t: Tenant, web: Web) -> None:
    collection = t.collection(default_risk="low")
    assert t.upload(collection, PAGE, filename="refunds.md").status_code == 202
    web.routes["docs.example.com/refunds.md"] = [httpx2.Response(200, content=PAGE)]
    r = _import(t, collection, "https://docs.example.com/refunds.md")
    _run(t)
    doc = _doc(t, r.json()["document"]["id"])
    assert doc.status == DocStatus.FAILED and "duplicate" in (doc.error or "")


@pytest.mark.parametrize(
    "form",
    [
        {"url": "ftp://docs.example.com/a.md"},
        {"url": "https:///a.md"},
        {"url": "https://docs.example.com/a.exe"},  # a type no engine reads: refused at once
        {},  # neither a file nor a URL
    ],
)
def test_a_bad_import_request_is_refused_at_once(t: Tenant, form: dict[str, str]) -> None:
    data = {"collection_id": t.collection(), **form}
    r = t.c.http.post("/v1/kb/documents", data=data, headers=t.host)
    assert r.status_code == 422, r.text


def test_a_file_and_a_url_together_are_refused(t: Tenant) -> None:
    r = t.c.http.post(
        "/v1/kb/documents",
        data={"collection_id": t.collection(), "url": "https://docs.example.com/a.md"},
        files={"file": ("a.md", PAGE, "text/markdown")},
        headers=t.host,
    )
    assert r.status_code == 422
