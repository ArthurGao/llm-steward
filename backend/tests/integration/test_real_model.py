"""Opt-in (`uv run pytest -m model`): the real multilingual MiniLM model, which the fake cannot
stand in for — it finds paraphrases across Chinese and English with no shared words."""

import uuid
from pathlib import Path

import pytest

from llm_steward.providers.base import Message, Scope
from llm_steward.providers.native.embedding import FastEmbedEmbedder
from llm_steward.providers.native.providers import NativeMemoryProvider, create_store
from llm_steward.storage.db import engine_scope

pytestmark = pytest.mark.model

MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
NS = uuid.UUID("00000000-0000-7000-8000-0000000000aa")
MEMORIES = {
    "refund": "退款审核需要三到五个工作日",
    "coffee": "The user drinks black coffee every morning",
    "city": "用户住在上海",
}


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("How long does a refund review take?", "refund"),  # English → Chinese memory
        ("用户早上喝什么", "coffee"),  # Chinese → English memory
        ("Which city does the user live in?", "city"),
    ],
)
async def test_cross_language_paraphrase_ranks_first(
    tmp_path: Path, query: str, expected: str
) -> None:
    embedder = FastEmbedEmbedder(MODEL)
    async with engine_scope(f"sqlite+aiosqlite:///{tmp_path / 'm.db'}", schema="unused") as engine:
        provider = NativeMemoryProvider(create_store(engine, embedder, "unused"), embedder)
        for key, text in MEMORIES.items():
            await provider.add(
                [Message("user", text)], Scope(NS), infer=False, metadata={"steward_id": key}
            )
        hits = await provider.search(query, Scope(NS), top_k=3)
    assert hits[0].external_id == expected


RERANKER = "BAAI/bge-reranker-v2-m3-int8"


async def test_the_real_cross_encoder_scores_the_answer_above_the_rest() -> None:
    """The multilingual cross-encoder (design §6 step 5; 0.57 GB, downloaded once)."""
    from llm_steward.providers.rerank import FastEmbedReranker

    reranker = FastEmbedReranker(RERANKER)
    texts = ["The user drinks black coffee", "退款审核需要三到五个工作日", "用户住在上海"]
    scores = await reranker.score("How long does a refund review take?", texts)
    assert len(scores) == 3 and scores.index(max(scores)) == 1
