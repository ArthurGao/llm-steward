"""P4 acceptance (design §11), first half: with a provider delayed 2 s, the response still comes
back within the timeout budget, marked degraded, with the other source's results. Both dialects;
the second half (recall@8 on a labelled set) is the opt-in eval in tests/eval/."""

import asyncio
import time
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import select

from llm_steward.models.enums import KbStatus
from llm_steward.models.tables import RetrievalLog
from llm_steward.services.governance_service import CreateCollection
from tests.conftest import AppClient, kb_entry_op, post_memory, running_app

DELAY_S = 2.0
TIMEOUT_MS = 800  # the default provider budget (design §6 step 2)


@pytest.fixture
def client(migrated_url: str) -> Iterator[AppClient]:
    with running_app(migrated_url, settings={"retrieve_timeout_ms": TIMEOUT_MS}) as c:
        yield c


def _slow(monkeypatch: pytest.MonkeyPatch, provider: Any) -> None:
    search = provider.search

    async def slow_search(*args: Any, **kwargs: Any) -> Any:
        await asyncio.sleep(DELAY_S)
        return await search(*args, **kwargs)

    monkeypatch.setattr(provider, "search", slow_search)


def _logged_latency(client: AppClient, retrieval_id: str) -> dict[str, Any]:
    """The per-stage timings the server wrote to the retrieval log for this request."""

    async def read() -> dict[str, Any]:
        async with client.sessions() as s:
            row = await s.scalar(
                select(RetrievalLog.latency_ms).where(RetrievalLog.id == uuid.UUID(retrieval_id))
            )
            return dict(row or {})

    return client.call(read)


@pytest.mark.parametrize("slow", ["kb", "memory"])
def test_a_provider_2s_late_is_cut_off_and_the_rest_still_served(
    client: AppClient, monkeypatch: pytest.MonkeyPatch, slow: str
) -> None:
    h, ns = client.new_tenant("acme")
    memory = post_memory(client, h, "refund pending for order 7")
    collection = client.apply(CreateCollection(ns, "faq"))
    entry = client.apply(kb_entry_op(ns, collection.id, KbStatus.ACTIVE))
    client.drain()
    _slow(monkeypatch, getattr(client.services.providers, slow))

    started = time.perf_counter()
    r = client.http.post("/v1/retrieve", json={"query": "refund"}, headers=h)
    elapsed = time.perf_counter() - started

    assert r.status_code == 200, r.text
    body = r.json()
    # The server's own stage timings say whether the cut-off or another stage was slow: once on CI
    # this took 2.17 s although the slow provider was cut off on time (its TimeoutError was logged).
    assert elapsed < 1.5, (
        f"{elapsed:.2f}s: not cut off at the {TIMEOUT_MS} ms budget; "
        f"server latency_ms={_logged_latency(client, body['retrieval_id'])}"
    )
    assert body["degraded"] == [slow]
    other = str(entry.id) if slow == "memory" else memory
    assert [i["id"] for i in body["items"]] == [other]  # the healthy source still answers
