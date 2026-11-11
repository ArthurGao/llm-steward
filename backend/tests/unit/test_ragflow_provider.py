"""RAGFlowProvider against a scripted RAGFlow v1.0.0 API (httpx2.MockTransport). The shapes here
were checked against the running v1.0.0 server (2026-11-11, KB global/ragflow); the real engine
runs in tests/contract/test_kb_contract.py under -m contract."""

import json
import uuid
from typing import Any, ClassVar

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
from llm_steward.providers.ragflow import RAGFlowProvider
from tests.fakes.http import Handler, Router, mock_http

BASE = "http://ragflow.test"
API = "/api/v1"
NS = uuid.UUID("00000000-0000-7000-8000-000000000001")
COLLECTION = uuid.UUID("00000000-0000-7000-8000-0000000000c1")
ENTRY = KbEntryDoc(uuid.UUID("00000000-0000-7000-8000-0000000000e1"), NS, "Refund", "3 days", 2)
DOCS = f"{API}/datasets/ds-1/documents"
ENTRY_NAME = f"entry-{ENTRY.entry_id}.txt"


def ok(data: Any = None) -> httpx2.Response:
    body: dict[str, Any] = {"code": 0, "message": "success"}
    if data is not None:
        body["data"] = data
    return httpx2.Response(200, json=body)


def code(value: int, message: str = "nope") -> httpx2.Response:
    """RAGFlow's way to fail: HTTP 200, a non-zero business code."""
    return httpx2.Response(200, json={"code": value, "message": message})


class Api(Router):
    not_found: ClassVar[dict[str, Any]] = {"code": 404, "message": "Resource not found"}


def provider(api: Api) -> RAGFlowProvider:
    return RAGFlowProvider(mock_http(api), BASE, "ragflow-test", timeout_s=1, budget_s=5)


def documents(by_param: dict[str, list[dict[str, Any]]]) -> Handler:
    """GET .../documents?name= or ?id=: the docs listed for that value; an unknown id or name is
    code 102, as v1.0.0 answers ("you don't own the document …")."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        params = request.url.params
        key = params.get("id") or params.get("name") or ""
        docs = by_param.get(key, [])
        if not docs:
            return code(102, f"you don't own the document {key}")
        return ok({"docs": docs, "total": len(docs), "has_active_tasks": False})

    return handler


# --- collections ---------------------------------------------------------------------------------


async def test_a_collection_is_a_dataset_named_after_it() -> None:
    api = Api(
        {
            ("GET", f"{API}/datasets"): lambda r: ok([]),
            ("POST", f"{API}/datasets"): lambda r: ok({"id": "ds-new"}),
        }
    )
    ref = KbCollectionRef(COLLECTION, NS, "Refunds")
    assert await provider(api).ensure_collection(ref) == "ds-new"
    assert api.requests[0].url.params["name"] == f"steward-{COLLECTION}"
    # exactly the fields v1.0.0 accepts: any other ("description") is code 101
    body = api.body("POST", f"{API}/datasets")
    assert body == {"name": f"steward-{COLLECTION}", "parse_type": 1, "parser_id": "general"}
    assert api.requests[0].headers["Authorization"] == "Bearer ragflow-test"


async def test_new_datasets_use_the_configured_parser() -> None:
    api = Api(
        {
            ("GET", f"{API}/datasets"): lambda r: ok([]),  # an unknown name: an empty list
            ("POST", f"{API}/datasets"): lambda r: ok({"id": "ds-new"}),
        }
    )
    kb = RAGFlowProvider(mock_http(api), BASE, "k", timeout_s=1, parser_id="paper")
    await kb.ensure_collection(KbCollectionRef(COLLECTION, NS, "Papers"))
    assert api.body("POST", f"{API}/datasets")["parser_id"] == "paper"


async def test_an_existing_dataset_is_found_not_created_again() -> None:
    """RAGFlow would not refuse a second one: it names it `steward-…(1)` (KB ragflow §2.2)."""
    listed = [{"id": "ds-1", "name": f"steward-{COLLECTION}"}]
    api = Api({("GET", f"{API}/datasets"): lambda r: ok(listed)})
    assert await provider(api).ensure_collection(KbCollectionRef(COLLECTION, NS, "x")) == "ds-1"
    assert api.calls() == [f"GET {API}/datasets"]


# --- entries -------------------------------------------------------------------------------------


async def test_a_new_entry_is_a_document_with_one_chunk_of_its_text() -> None:
    api = Api(
        {
            ("GET", DOCS): documents({}),
            ("POST", DOCS): lambda r: ok([{"id": "doc-1", "name": ENTRY_NAME}]),
            ("POST", f"{DOCS}/doc-1/chunks"): lambda r: ok({"chunk": {"id": "ch-1"}}),
        }
    )
    assert await provider(api).upsert_entry(ENTRY, "ds-1") == "ds-1/doc-1"
    upload = next(r for r in api.requests if (r.method, r.url.path) == ("POST", DOCS))
    assert ENTRY_NAME.encode() in upload.content  # the multipart file name
    assert api.body("POST", f"{DOCS}/doc-1/chunks") == {"content": "Refund\n3 days"}


async def test_an_entry_is_replaced_by_patching_its_chunk() -> None:
    chunks = {"chunks": [{"id": "ch-1", "content": "Refund\n3 days"}], "total": 1}
    api = Api(
        {
            ("GET", DOCS): documents({"doc-1": [{"id": "doc-1", "name": ENTRY_NAME}]}),
            ("GET", f"{DOCS}/doc-1/chunks"): lambda r: ok(chunks),
            ("PATCH", f"{DOCS}/doc-1/chunks/ch-1"): lambda r: ok(),
        }
    )
    again = KbEntryDoc(ENTRY.entry_id, NS, "Refund", "7 days", 3)
    assert await provider(api).upsert_entry(again, "ds-1", "ds-1/doc-1") == "ds-1/doc-1"
    assert api.body("PATCH", f"{DOCS}/doc-1/chunks/ch-1") == {"content": "Refund\n7 days"}
    assert f"POST {DOCS}" not in api.calls()


async def test_an_entry_deleted_in_ragflow_is_created_again() -> None:
    api = Api(
        {
            ("GET", DOCS): documents({}),  # its id: code 102; its name: none
            ("POST", DOCS): lambda r: ok([{"id": "doc-2", "name": ENTRY_NAME}]),
            ("POST", f"{DOCS}/doc-2/chunks"): lambda r: ok({"chunk": {"id": "ch-2"}}),
        }
    )
    assert await provider(api).upsert_entry(ENTRY, "ds-1", "ds-1/doc-1") == "ds-1/doc-2"


async def test_after_a_crash_the_entrys_document_is_found_by_name_not_uploaded_twice() -> None:
    """Uploaded, then the worker died before recording the id: the retry finds it by name."""
    api = Api(
        {
            ("GET", DOCS): documents({ENTRY_NAME: [{"id": "doc-1", "name": ENTRY_NAME}]}),
            ("GET", f"{DOCS}/doc-1/chunks"): lambda r: ok({"chunks": [], "total": 0}),
            ("POST", f"{DOCS}/doc-1/chunks"): lambda r: ok({"chunk": {"id": "ch-1"}}),
        }
    )
    assert await provider(api).upsert_entry(ENTRY, "ds-1") == "ds-1/doc-1"
    assert f"POST {DOCS}" not in api.calls()


async def test_remove_deletes_the_document_and_a_second_remove_is_fine() -> None:
    deleted: list[str] = []

    def delete(request: httpx2.Request) -> httpx2.Response:
        ids = json.loads(request.content)["ids"]
        if deleted:  # already gone: v1.0.0 answers code 102
            return code(102, "These documents do not belong to dataset ds-1")
        deleted.extend(ids)
        return ok({"deleted": 1})

    api = Api({("DELETE", DOCS): delete, ("GET", DOCS): documents({})})
    kb = provider(api)
    await kb.remove("ds-1/doc-1")
    await kb.remove("ds-1/doc-1")
    assert deleted == ["doc-1"]
    bodies = [json.loads(r.content) for r in api.requests if r.method == "DELETE"]
    assert bodies == [{"ids": ["doc-1"]}, {"ids": ["doc-1"]}]  # never delete_all


async def test_a_failed_remove_of_a_document_that_still_exists_is_an_error() -> None:
    api = Api(
        {
            ("DELETE", DOCS): lambda r: code(103, "busy"),
            ("GET", DOCS): documents({"doc-1": [{"id": "doc-1"}]}),
        }
    )
    with pytest.raises(ProviderUnavailable, match="code 103"):
        await provider(api).remove("ds-1/doc-1")


# --- search --------------------------------------------------------------------------------------


async def test_search_asks_for_candidates_and_groups_chunks_by_document() -> None:
    chunks = [
        {"dataset_id": "ds-1", "document_id": "doc-1", "content": "a1", "similarity": 0.9},
        {"dataset_id": "ds-2", "document_id": "doc-2", "content": "b1", "similarity": 0.8},
        {"dataset_id": "ds-1", "document_id": "doc-1", "content": "a2", "similarity": 0.7},
    ]
    api = Api({("POST", f"{API}/retrieval"): lambda r: ok({"chunks": chunks, "total": 3})})
    hits = await provider(api).search("refunds", ["ds-1", "ds-2"], top_k=5)
    assert [(h.external_id, h.score, h.text, h.passages) for h in hits] == [
        ("ds-1/doc-1", 0.9, "a1", ("a1", "a2")),
        ("ds-2/doc-2", 0.8, "b1", ("b1",)),
    ]
    body = api.body("POST", f"{API}/retrieval")
    assert body["question"] == "refunds" and body["dataset_ids"] == ["ds-1", "ds-2"]
    # governance ranks and decides: no engine floor; the vector side leads (measured, KB §2.4)
    assert (body["similarity_threshold"], body["keywords_similarity_weight"]) == (0.0, 0.3)
    assert body["page_size"] == 5


async def test_search_of_no_collections_asks_nothing() -> None:
    api = Api({})
    assert await provider(api).search("x", [], top_k=3) == []
    assert api.requests == []


@pytest.mark.parametrize(
    ("response", "match"),
    [
        (code(102, "bad: alice@x.org"), "code 102"),
        (httpx2.Response(500, json={"code": 500, "message": "alice@x.org"}), "HTTP 500"),
        (httpx2.Response(502, text="<html>alice@x.org</html>"), "HTTP 502"),
    ],
)
async def test_errors_are_reported_without_the_response_message(
    response: httpx2.Response, match: str
) -> None:
    """HTTP 200 is not success in v1.0.0; and its message can echo what it was sent."""
    api = Api({("POST", f"{API}/retrieval"): lambda r: response})
    with pytest.raises(ProviderUnavailable, match=match) as caught:
        await provider(api).search("x", ["ds-1"], top_k=1)
    assert "alice" not in str(caught.value)


async def test_health_reads_the_engines_own_status() -> None:
    healthy = {"db": "ok", "doc_engine": "ok", "status": "ok"}
    sick = {"db": "ok", "doc_engine": "timeout", "status": "nok"}
    for body, expected in ((healthy, "ok"), (sick, "error")):
        api = Api(
            {("GET", f"{API}/system/healthz"): lambda r, b=body: httpx2.Response(200, json=b)}
        )
        assert (await provider(api).health()).status == expected


def test_capabilities() -> None:
    p = provider(Api({}))
    assert (p.name, p.capabilities) == ("ragflow", frozenset({ENTRY_UPSERT, DOC_PARSING}))


# --- documents -----------------------------------------------------------------------------------

DOCUMENT = KbDocumentUpload(
    uuid.UUID("00000000-0000-7000-8000-0000000000d1"), NS, "Policy", "policy.md", None, b"# Refunds"
)
DOC_NAME = f"{DOCUMENT.document_id}-policy.md"


async def test_a_document_is_uploaded_under_its_own_name_then_parsing_is_started() -> None:
    api = Api(
        {
            ("GET", DOCS): documents({}),
            ("POST", DOCS): lambda r: ok([{"id": "doc-9", "name": DOC_NAME}]),
            ("POST", f"{API}/datasets/ds-1/chunks"): lambda r: ok(),
        }
    )
    assert await provider(api).ingest_document(DOCUMENT, "ds-1") == "ds-1/doc-9"
    upload = next(r for r in api.requests if (r.method, r.url.path) == ("POST", DOCS))
    assert DOC_NAME.encode() in upload.content
    assert api.body("POST", f"{API}/datasets/ds-1/chunks") == {"document_ids": ["doc-9"]}


async def test_uploading_the_same_document_again_returns_the_copy_ragflow_has() -> None:
    """RAGFlow keeps a second copy of the same bytes (KB §2.2): the name is what makes it ours."""
    held = [{"id": "doc-9", "name": DOC_NAME, "ingestion_status": "RUNNING"}]
    api = Api({("GET", DOCS): documents({DOC_NAME: held})})
    assert await provider(api).ingest_document(DOCUMENT, "ds-1") == "ds-1/doc-9"
    assert api.calls() == [f"GET {DOCS}"]


async def test_a_held_copy_that_never_started_parsing_is_started_now() -> None:
    held = [{"id": "doc-9", "name": DOC_NAME, "ingestion_status": "UNSTART"}]
    api = Api(
        {
            ("GET", DOCS): documents({DOC_NAME: held}),
            ("POST", f"{API}/datasets/ds-1/chunks"): lambda r: ok(),
        }
    )
    assert await provider(api).ingest_document(DOCUMENT, "ds-1") == "ds-1/doc-9"
    assert f"POST {API}/datasets/ds-1/chunks" in api.calls()


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("UNSTART", DocStatus.PENDING),
        ("CREATED", DocStatus.PENDING),
        ("SCHEDULED", DocStatus.PENDING),
        ("RUNNING", DocStatus.PROCESSING),
        ("STOPPING", DocStatus.PROCESSING),
        ("COMPLETED", DocStatus.READY),
        ("FAILED", DocStatus.FAILED),
        ("STOPPED", DocStatus.FAILED),
    ],
)
async def test_the_ingestion_status_maps_to_a_document_status(
    status: str, expected: DocStatus
) -> None:
    api = Api({("GET", DOCS): documents({"doc-9": [{"id": "doc-9", "ingestion_status": status}]})})
    assert await provider(api).document_status("ds-1/doc-9") == expected


async def test_a_document_gone_from_ragflow_has_failed() -> None:
    api = Api({("GET", DOCS): documents({})})
    assert await provider(api).document_status("ds-1/doc-9") == DocStatus.FAILED


async def test_an_external_id_without_its_dataset_is_refused() -> None:
    with pytest.raises(ProviderUnavailable, match="not a ragflow id"):
        await provider(Api({})).remove("doc-1")
