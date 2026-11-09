"""WeKnoraProvider against a scripted WeKnora v0.9.0 API (httpx2.MockTransport). The shapes here
were checked against the running v0.9.0 server (2026-11-09); the real engine runs in
tests/contract/test_kb_contract.py under -m contract."""

import json
import uuid
from collections.abc import Callable
from typing import Any

import httpx2
import pytest

from llm_steward.errors import ProviderUnavailable
from llm_steward.models.enums import DocStatus
from llm_steward.providers.base import (
    DOC_PARSING,
    ENTRY_UPSERT,
    KbCollectionRef,
    KbDocumentUpload,
    KbEntryDoc,
)
from llm_steward.providers.weknora import WeKnoraProvider
from tests.fakes.http import mock_http

BASE = "http://weknora.test"
NS = uuid.UUID("00000000-0000-7000-8000-000000000001")
COLLECTION = uuid.UUID("00000000-0000-7000-8000-0000000000c1")
ENTRY = KbEntryDoc(uuid.UUID("00000000-0000-7000-8000-0000000000e1"), NS, "Refund", "3 days", 2)


def ok(data: Any, status: int = 200) -> httpx2.Response:
    return httpx2.Response(status, json={"success": True, "data": data})


class Api:
    """Routes (method, path) to a handler; records every request."""

    def __init__(self, routes: dict[tuple[str, str], Callable[[httpx2.Request], httpx2.Response]]):
        self.routes = routes
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        handler = self.routes.get((request.method, request.url.path))
        if handler is None:
            return httpx2.Response(404, json={"success": False, "error": {"code": 1004}})
        return handler(request)

    def calls(self) -> list[str]:
        return [f"{r.method} {r.url.path}" for r in self.requests]

    def body(self, method: str, path: str) -> dict[str, Any]:
        [request] = [r for r in self.requests if (r.method, r.url.path) == (method, path)]
        return json.loads(request.content)  # type: ignore[no-any-return]


def provider(api: Api, *, model: str | None = "model-1", budget_s: float = 5) -> WeKnoraProvider:
    return WeKnoraProvider(
        mock_http(api), BASE, "sk-test", model, timeout_s=1, budget_s=budget_s, first_poll_s=0
    )


def statuses(*values: str) -> Callable[[httpx2.Request], httpx2.Response]:
    """GET /knowledge/{id}: parse_status values in turn, the last one repeated."""
    seq = list(values)

    def handler(request: httpx2.Request) -> httpx2.Response:
        value = seq.pop(0) if len(seq) > 1 else seq[0]
        return ok({"id": request.url.path.rsplit("/", 1)[1], "parse_status": value})

    return handler


async def test_a_collection_is_a_knowledge_base_on_the_configured_model() -> None:
    api = Api(
        {
            ("GET", "/api/v1/knowledge-bases"): lambda r: ok([{"id": "x", "name": "other"}]),
            ("POST", "/api/v1/knowledge-bases"): lambda r: ok({"id": "kb-1"}, 201),
        }
    )
    kb_id = await provider(api).ensure_collection(KbCollectionRef(COLLECTION, NS, "Refunds FAQ"))
    assert kb_id == "kb-1"
    assert all(r.headers["X-API-Key"] == "sk-test" for r in api.requests)
    body = api.body("POST", "/api/v1/knowledge-bases")
    assert (body["name"], body["description"], body["embedding_model_id"]) == (
        f"steward-{COLLECTION}",
        "Refunds FAQ",
        "model-1",
    )


async def test_an_existing_knowledge_base_is_found_not_created_again() -> None:
    """A crash after creating it but before recording its id must not make a second one."""
    name = f"steward-{COLLECTION}"
    api = Api({("GET", "/api/v1/knowledge-bases"): lambda r: ok([{"id": "kb-1", "name": name}])})
    assert await provider(api).ensure_collection(KbCollectionRef(COLLECTION, NS, "c")) == "kb-1"
    assert api.calls() == ["GET /api/v1/knowledge-bases"]


async def test_without_an_embedding_model_no_collection_is_created() -> None:
    api = Api({})
    with pytest.raises(ProviderUnavailable, match="STEWARD_WEKNORA_EMBEDDING_MODEL_ID"):
        await provider(api, model=None).ensure_collection(KbCollectionRef(COLLECTION, NS, "c"))
    assert api.requests == []


async def test_a_new_entry_is_published_tagged_and_waited_for() -> None:
    api = Api(
        {
            ("POST", "/api/v1/knowledge-bases/kb-1/knowledge/manual"): lambda r: ok(
                {"id": "k-1", "parse_status": "pending"}
            ),
            ("PUT", "/api/v1/knowledge/k-1"): lambda r: ok({"id": "k-1"}),
            ("GET", "/api/v1/knowledge/k-1"): statuses("pending", "processing", "finalizing"),
        }
    )
    assert await provider(api).upsert_entry(ENTRY, "kb-1") == "k-1"
    created = api.body("POST", "/api/v1/knowledge-bases/kb-1/knowledge/manual")
    assert created == {"title": "Refund", "content": "3 days", "status": "publish"}  # never a draft
    tagged = api.body("PUT", "/api/v1/knowledge/k-1")
    assert tagged == {"custom_metadata": {"steward_id": str(ENTRY.entry_id), "version": 2}}
    assert api.calls().count("GET /api/v1/knowledge/k-1") == 3  # searchable before returning


async def test_an_existing_entry_is_replaced_by_its_weknora_id() -> None:
    api = Api(
        {
            ("PUT", "/api/v1/knowledge/manual/k-1"): lambda r: ok({"id": "k-1"}),
            ("PUT", "/api/v1/knowledge/k-1"): lambda r: ok({"id": "k-1"}),
            ("GET", "/api/v1/knowledge/k-1"): statuses("completed"),
        }
    )
    assert await provider(api).upsert_entry(ENTRY, "kb-1", existing_id="k-1") == "k-1"
    assert "POST /api/v1/knowledge-bases/kb-1/knowledge/manual" not in api.calls()
    assert api.body("PUT", "/api/v1/knowledge/manual/k-1")["status"] == "publish"


async def test_an_entry_deleted_in_weknora_is_created_again() -> None:
    api = Api(
        {
            ("POST", "/api/v1/knowledge-bases/kb-1/knowledge/manual"): lambda r: ok({"id": "k-2"}),
            ("PUT", "/api/v1/knowledge/k-2"): lambda r: ok({"id": "k-2"}),
            ("GET", "/api/v1/knowledge/k-2"): statuses("completed"),
        }
    )  # PUT /knowledge/manual/k-1 is not routed: 404
    assert await provider(api).upsert_entry(ENTRY, "kb-1", existing_id="k-1") == "k-2"


async def test_a_new_entry_that_fails_to_parse_is_removed_and_reported() -> None:
    deleted = False

    def failed(request: httpx2.Request) -> httpx2.Response:
        if deleted:
            return httpx2.Response(404, json={"success": False})
        return ok({"id": "k-1", "parse_status": "failed", "error_message": "embedding timeout"})

    def delete(request: httpx2.Request) -> httpx2.Response:
        nonlocal deleted
        deleted = True
        return ok({"task_id": "t"})

    api = Api(
        {
            ("POST", "/api/v1/knowledge-bases/kb-1/knowledge/manual"): lambda r: ok({"id": "k-1"}),
            ("PUT", "/api/v1/knowledge/k-1"): lambda r: ok({"id": "k-1"}),
            ("GET", "/api/v1/knowledge/k-1"): failed,
            ("DELETE", "/api/v1/knowledge/k-1"): delete,
        }
    )
    with pytest.raises(ProviderUnavailable, match="could not parse k-1") as caught:
        await provider(api).upsert_entry(ENTRY, "kb-1")
    assert "embedding timeout" not in str(caught.value)  # WeKnora's message may echo content
    assert "DELETE /api/v1/knowledge/k-1" in api.calls()  # a retry starts clean, no duplicate


async def test_a_new_entry_that_cannot_be_tagged_is_removed_too() -> None:
    api = Api(
        {
            ("POST", "/api/v1/knowledge-bases/kb-1/knowledge/manual"): lambda r: ok({"id": "k-1"}),
            ("PUT", "/api/v1/knowledge/k-1"): lambda r: httpx2.Response(500, json={}),
            ("DELETE", "/api/v1/knowledge/k-1"): lambda r: ok({"task_id": "t"}),
        }
    )
    with pytest.raises(ProviderUnavailable, match="HTTP 500"):
        await provider(api).upsert_entry(ENTRY, "kb-1")
    assert api.calls()[-1] == "DELETE /api/v1/knowledge/k-1"


async def test_slow_parsing_still_returns_the_id_so_a_retry_never_duplicates() -> None:
    api = Api(
        {
            ("POST", "/api/v1/knowledge-bases/kb-1/knowledge/manual"): lambda r: ok({"id": "k-1"}),
            ("PUT", "/api/v1/knowledge/k-1"): lambda r: ok({"id": "k-1"}),
            ("GET", "/api/v1/knowledge/k-1"): statuses("processing"),
        }
    )
    assert await provider(api, budget_s=0).upsert_entry(ENTRY, "kb-1") == "k-1"


async def test_remove_waits_until_weknoras_background_delete_is_done() -> None:
    lookups = iter([ok({"id": "k-1", "parse_status": "deleting"})])

    def get(request: httpx2.Request) -> httpx2.Response:
        return next(lookups, httpx2.Response(404, json={"success": False}))

    api = Api(
        {
            ("DELETE", "/api/v1/knowledge/k-1"): lambda r: ok({"task_id": "t"}),
            ("GET", "/api/v1/knowledge/k-1"): get,
        }
    )
    p = provider(api)
    await p.remove("k-1")
    assert api.calls() == [
        "DELETE /api/v1/knowledge/k-1",
        "GET /api/v1/knowledge/k-1",  # still there: deleting
        "GET /api/v1/knowledge/k-1",  # gone
    ]
    await p.remove("k-gone")  # 404 at once: already gone, nothing to wait for
    assert api.calls()[-1] == "DELETE /api/v1/knowledge/k-gone"


async def test_a_remove_that_does_not_finish_in_time_is_retried_not_trusted() -> None:
    api = Api(
        {
            ("DELETE", "/api/v1/knowledge/k-1"): lambda r: ok({"task_id": "t"}),
            ("GET", "/api/v1/knowledge/k-1"): lambda r: ok({"parse_status": "deleting"}),
        }
    )
    with pytest.raises(ProviderUnavailable, match="not deleted"):  # the Syncer retries (404-safe)
        await provider(api, budget_s=0).remove("k-1")


async def test_search_returns_each_items_best_chunks_across_knowledge_bases() -> None:
    hits = [
        {"id": "c2", "knowledge_id": "k-1", "score": 0.9, "content": "best chunk"},
        {"id": "c1", "knowledge_id": "k-1", "score": 0.4, "content": "weak chunk"},  # later, worse
        {"id": "c3", "knowledge_id": "k-2", "score": 0.7, "content": "other"},
        {"id": "c4", "knowledge_id": "k-3", "score": 0.1, "content": "cut by top_k"},
    ]
    api = Api({("POST", "/api/v1/knowledge-search"): lambda r: ok(hits)})
    found = await provider(api).search("refunds", ["kb-1", "kb-2"], top_k=2)
    assert [(h.external_id, h.score, h.text, h.passages) for h in found] == [
        ("k-1", 0.9, "best chunk", ("best chunk", "weak chunk")),  # its chunks come along
        ("k-2", 0.7, "other", ("other",)),
    ]
    body = api.body("POST", "/api/v1/knowledge-search")
    assert (body["query"], body["knowledge_base_ids"]) == ("refunds", ["kb-1", "kb-2"])
    # chunks; over-fetching is retrieval's (it asks for top_k x 3 already): not multiplied here
    assert body["match_count"] == 2
    assert await provider(api).search("x", [], top_k=5) == []


@pytest.mark.parametrize(
    ("response", "match"),
    [
        (httpx2.Response(404, json={"success": False}), "HTTP 404"),
        (httpx2.Response(401, json={"error": "Unauthorized"}), "HTTP 401 Unauthorized"),
        (
            httpx2.Response(
                200,
                json={"success": False, "error": {"code": 1010, "message": "bad: alice@x.org"}},
            ),
            "error code 1010",
        ),
    ],
)
async def test_errors_are_reported_without_the_response_body(
    response: httpx2.Response, match: str
) -> None:
    api = Api({("POST", "/api/v1/knowledge-search"): lambda r: response})
    with pytest.raises(ProviderUnavailable, match=match) as caught:
        await provider(api).search("x", ["kb-1"], top_k=1)
    assert "alice" not in str(caught.value)  # WeKnora's message can echo what it was sent


def test_capabilities() -> None:
    p = provider(Api({}))
    assert (p.name, p.capabilities) == ("weknora", frozenset({ENTRY_UPSERT, DOC_PARSING}))


DOCUMENT = KbDocumentUpload(
    uuid.UUID("00000000-0000-7000-8000-0000000000d1"), NS, "Policy", "policy.md", None, b"# Refunds"
)
UPLOAD = "/api/v1/knowledge-bases/kb-1/knowledge/file"


async def test_a_document_is_uploaded_as_a_file_then_tagged_without_waiting() -> None:
    api = Api(
        {
            ("POST", UPLOAD): lambda r: ok({"id": "k-9", "parse_status": "pending"}),
            ("PUT", "/api/v1/knowledge/k-9"): lambda r: ok({"id": "k-9"}),
        }
    )
    assert await provider(api).ingest_document(DOCUMENT, "kb-1") == "k-9"
    assert api.calls() == [f"POST {UPLOAD}", "PUT /api/v1/knowledge/k-9"]  # no status polling
    upload = api.requests[0]
    assert upload.headers["Content-Type"].startswith("multipart/form-data")
    content = upload.content
    assert b'name="file"; filename="policy.md"' in content and b"# Refunds" in content
    assert b'name="fileName"' in content
    assert b"application/octet-stream" in content  # no mime given
    assert api.body("PUT", "/api/v1/knowledge/k-9") == {
        "custom_metadata": {"steward_id": str(DOCUMENT.document_id), "kind": "document"}
    }


async def test_uploading_the_same_file_again_returns_the_copy_weknora_already_has() -> None:
    duplicate = httpx2.Response(
        409,
        json={
            "success": False,
            "code": "duplicate_file",
            "message": "File already exists: policy.md",
            "data": {"id": "k-1", "parse_status": "completed"},
        },
    )
    api = Api(
        {
            ("POST", UPLOAD): lambda r: duplicate,
            ("PUT", "/api/v1/knowledge/k-1"): lambda r: ok({"id": "k-1"}),
        }
    )
    assert await provider(api).ingest_document(DOCUMENT, "kb-1") == "k-1"


async def test_a_conflict_without_the_existing_item_is_an_error() -> None:
    api = Api({("POST", UPLOAD): lambda r: httpx2.Response(409, json={"success": False})})
    with pytest.raises(ProviderUnavailable, match="HTTP 409"):
        await provider(api).ingest_document(DOCUMENT, "kb-1")


@pytest.mark.parametrize(
    ("parse_status", "expected"),
    [
        ("pending", DocStatus.PENDING),
        ("processing", DocStatus.PROCESSING),
        ("finalizing", DocStatus.READY),
        ("completed", DocStatus.READY),
        ("failed", DocStatus.FAILED),
        ("cancelled", DocStatus.FAILED),
        ("deleting", DocStatus.FAILED),
        ("something-new", DocStatus.PROCESSING),
    ],
)
async def test_the_parse_status_maps_to_a_document_status(
    parse_status: str, expected: DocStatus
) -> None:
    api = Api({("GET", "/api/v1/knowledge/k-1"): statuses(parse_status)})
    assert await provider(api).document_status("k-1") == expected


async def test_a_document_gone_from_weknora_has_failed() -> None:
    assert await provider(Api({})).document_status("k-1") == DocStatus.FAILED  # 404
