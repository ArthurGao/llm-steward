"""The local cross-encoder adapter: an unknown model fails at startup, without a download."""

import asyncio
import contextlib
import threading
import time

import pytest

from llm_steward.config import Settings
from llm_steward.providers.registry import build_reranker
from llm_steward.providers.rerank import FastEmbedReranker


def test_no_model_means_no_reranker() -> None:
    assert build_reranker(Settings(rerank_model=None)) is None


def test_an_unknown_model_is_refused_at_startup() -> None:
    with pytest.raises(ValueError, match="no cross-encoder"):
        build_reranker(Settings(rerank_model="acme/not-a-reranker"))


def test_a_known_model_is_named_but_not_loaded() -> None:
    reranker = build_reranker(Settings(rerank_model="BAAI/bge-reranker-v2-m3-int8"))
    assert reranker is not None and reranker.model == "BAAI/bge-reranker-v2-m3-int8"
    assert reranker._impl is None  # type: ignore[attr-defined]  # downloads on first use only


class _SlowModel:
    """Stands in for a fastembed model that takes a while to load (download, ONNX start)."""

    loads = 0

    def __init__(self) -> None:
        time.sleep(0.3)
        _SlowModel.loads += 1

    def rerank(self, query: str, texts: list[str]) -> list[float]:
        return [0.0 for _ in texts]


async def test_a_load_cut_short_by_a_timeout_is_kept_not_restarted() -> None:
    """The first request times out while the model loads: the load goes on, and later requests
    use it instead of starting another one (a cold load longer than the budget would otherwise
    never finish)."""
    reranker = FastEmbedReranker("BAAI/bge-reranker-v2-m3-int8")
    _SlowModel.loads = 0
    reranker._load = _SlowModel  # type: ignore[method-assign]
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.05):
            await reranker.score("q", ["a"])
    await asyncio.sleep(0.5)  # the load finishes in the background
    assert await reranker.score("q", ["a", "b"]) == [0.5, 0.5]  # relevance in [0, 1]
    assert _SlowModel.loads == 1


async def test_a_failed_load_is_retried_by_the_next_caller() -> None:
    reranker = FastEmbedReranker("BAAI/bge-reranker-v2-m3-int8")
    attempts: list[int] = []

    def load() -> _SlowModel:
        attempts.append(1)
        if len(attempts) == 1:
            raise OSError("download interrupted")
        return _SlowModel()

    reranker._load = load  # type: ignore[method-assign]
    with pytest.raises(OSError):
        await reranker.score("q", ["a"])
    assert await reranker.score("q", ["a"]) == [0.5]
    assert len(attempts) == 2


async def test_one_cross_encoder_pass_runs_at_a_time() -> None:
    """An ONNX cross-encoder already uses every core: parallel passes only slow each other."""
    reranker = FastEmbedReranker("BAAI/bge-reranker-v2-m3-int8")
    running = peak = 0
    lock = threading.Lock()

    class Busy:
        def rerank(self, query: str, texts: list[str]) -> list[float]:
            nonlocal running, peak
            with lock:
                running += 1
                peak = max(peak, running)
            time.sleep(0.05)
            with lock:
                running -= 1
            return [0.0 for _ in texts]

    reranker._load = Busy  # type: ignore[method-assign]
    await asyncio.gather(*(reranker.score("q", ["a"]) for _ in range(4)))
    assert peak == 1


async def test_callers_that_time_out_do_not_let_more_passes_run_at_once() -> None:
    """A caller's timeout ends its wait, not a pass already running: the next pass still waits
    for it (else timed-out requests stack up ONNX threads), and a pass that was only queued when
    its caller gave up never runs."""
    reranker = FastEmbedReranker("BAAI/bge-reranker-v2-m3-int8")
    running = peak = runs = 0
    lock = threading.Lock()

    class Busy:
        def rerank(self, query: str, texts: list[str]) -> list[float]:
            nonlocal running, peak, runs
            with lock:
                running += 1
                runs += 1
                peak = max(peak, running)
            time.sleep(0.1)
            with lock:
                running -= 1
            return [0.0 for _ in texts]

    reranker._load = Busy  # type: ignore[method-assign]
    await reranker.score("q", ["warm"])  # loaded (one run)

    async def impatient(start: float) -> None:
        await asyncio.sleep(start)  # requests arrive one after another
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(0.01):
                await reranker.score("q", ["a"])

    await asyncio.gather(*(impatient(i * 0.02) for i in range(6)))
    await asyncio.sleep(0.3)  # what did start finishes
    assert peak == 1
    assert runs <= 3  # warm-up, the first, at most one more: the queued ones were dropped
