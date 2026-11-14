"""The eval tests' tenant: a running app with the real local embedding and reranker."""

import uuid
from collections.abc import Iterator

import pytest

from llm_steward.providers.native.embedding import FastEmbedEmbedder
from tests.conftest import Tenant, running_app
from tests.eval.target import EMBEDDING, RERANKER


@pytest.fixture
def db(migrated_url: str) -> str:
    return "sqlite" if migrated_url.startswith("sqlite") else "pg"


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
