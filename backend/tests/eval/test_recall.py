"""Opt-in (`uv run pytest -m eval`): recall@8 of joint retrieval on the SYNTHETIC labelled set
(tests/eval/corpus.py), through the API, on the native providers with the real local embedding
(multilingual MiniLM). Design §11 P4 acceptance; the threshold sits just under the measured
baseline (design §11 records it), so a scoring change that loses recall fails here."""

import uuid
from collections.abc import Iterator
from typing import Any

import pytest

from llm_steward.providers.native.embedding import FastEmbedEmbedder
from tests.conftest import Tenant, running_app
from tests.eval.corpus import DOCUMENTS, ENTRIES, MEMORIES, QUERIES

pytestmark = pytest.mark.eval

EMBEDDING = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
RERANKER = "BAAI/bge-reranker-v2-m3-int8"
TOP_K = 8
USER = "u1"
THRESHOLD = 0.92  # baseline 0.960 (rerank 0.980), 2026-11-09: one fully missed query of slack


def _load(t: Tenant) -> dict[str, str]:
    """The corpus through the API; item id -> key."""
    keys: dict[str, str] = {}
    collection = t.collection(default_risk="low")
    for key, title, content in ENTRIES:
        r = t.post(
            "/v1/kb/entries", {"collection_id": collection, "title": title, "content": content}
        )
        assert r.status_code == 201, r.text
        keys[r.json()["entry"]["id"]] = key
    for key, name, text in DOCUMENTS:
        r = t.c.http.post(
            "/v1/kb/documents",
            data={"collection_id": collection},
            files={"file": (name, text.encode(), "text/markdown")},
            headers=t.host,
        )
        assert r.status_code == 202, r.text
        keys[r.json()["document"]["id"]] = key
    for key, content in MEMORIES:
        r = t.c.http.post(
            "/v1/memories", json={"content": content, "user_id": USER}, headers=t.host
        )
        assert r.status_code == 201, r.text
        keys[r.json()["id"]] = key
    t.c.drain()
    return keys


def _recall(t: Tenant, keys: dict[str, str], **body: Any) -> tuple[float, list[str]]:
    """Mean recall@TOP_K over the queries, and the queries that missed something."""
    scores, misses = [], []
    for query, expected in QUERIES:
        payload = {"query": query, "scope": {"user_id": USER}, "top_k": TOP_K, **body}
        r = t.c.http.post("/v1/retrieve", json={**payload, "include_pinned": False}, headers=t.host)
        assert r.status_code == 200, r.text
        assert r.json()["degraded"] == [], r.text
        got = {keys.get(i["id"]) for i in r.json()["items"]}
        found = len(set(expected) & got) / len(expected)
        scores.append(found)
        if found < 1:
            misses.append(f"{query!r}: wanted {expected}, got {sorted(k for k in got if k)}")
    return sum(scores) / len(scores), misses


@pytest.fixture
def tenant(migrated_url: str) -> Iterator[Tenant]:
    embedder = FastEmbedEmbedder(EMBEDDING)
    settings = {
        "retrieve_timeout_ms": 30_000,
        "rerank_model": RERANKER,
        "rerank_timeout_ms": 60_000,
    }
    with running_app(migrated_url, settings=settings, embedder=embedder) as c:
        yield Tenant(c, f"eval-{uuid.uuid4().hex[:6]}")


def test_recall_at_8(tenant: Tenant, capsys: pytest.CaptureFixture[str]) -> None:
    keys = _load(tenant)
    recall, misses = _recall(tenant, keys)
    reranked, rerank_misses = _recall(tenant, keys, rerank=True)
    with capsys.disabled():
        print(
            f"\nrecall@{TOP_K}: {recall:.3f} (rerank: {reranked:.3f}) over {len(QUERIES)} queries"
        )
        for miss in misses:
            print("  miss:", miss)
        for miss in rerank_misses:
            print("  miss (rerank):", miss)
    assert recall >= THRESHOLD, misses
