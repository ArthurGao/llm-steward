"""The request middleware that wakes an idle outbox worker (worker idle mode)."""

import asyncio
from types import SimpleNamespace
from typing import Any

from llm_steward.main import WakesWorker


def test_a_request_touches_the_worker_when_it_starts_and_when_it_answers() -> None:
    # Codex: a request touching only on entry can outlast the idle timeout; a job it enqueued
    # then waits for the next request. Its answer starts after its transaction committed.
    events: list[str] = []
    worker = SimpleNamespace(touch=lambda: events.append("touch"))
    app = SimpleNamespace(state=SimpleNamespace(services=SimpleNamespace(worker=worker)))

    async def handler(scope: Any, receive: Any, send: Any) -> None:
        events.append("handled")
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def send(message: Any) -> None:
        events.append(message["type"])

    asyncio.run(WakesWorker(handler)({"type": "http", "app": app}, None, send))
    assert events == [
        "touch", "handled", "touch", "http.response.start", "http.response.body"
    ]  # fmt: skip
