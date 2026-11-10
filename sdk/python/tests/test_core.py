"""The client's request rules, against a fake network: errors, retries, Idempotency-Key."""

import asyncio
import uuid
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from llm_steward_client import (
    AsyncSteward,
    AuthenticationError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    ServerError,
    Steward,
    StewardConnectionError,
    StewardError,
    UnprocessableError,
    models,
)

MEMORY: dict[str, Any] = {
    "id": str(uuid.uuid4()),
    "content": "likes green tea",
    "user_id": "u1",
    "agent_id": None,
    "session_id": None,
    "memory_type": "semantic",
    "status": "active",
    "origin": "direct",
    "pinned": False,
    "importance": 0.5,
    "confidence": 0.8,
    "tags": [],
    "metadata": {},
    "source_refs": [],
    "sync_status": "pending",
    "valid_from": "2026-11-10T00:00:00Z",
    "valid_to": None,
    "created_at": "2026-11-10T00:00:00Z",
    "updated_at": "2026-11-10T00:00:00Z",
}


def problem(status: int, code: str, detail: str = "nope", **headers: str) -> httpx.Response:
    body = {"type": f"urn:llm-steward:problem:{code}", "status": status, "code": code}
    return httpx.Response(status, json={**body, "detail": detail}, headers=headers)


class Network:
    def __init__(self, *answers: httpx.Response | Exception) -> None:
        self.answers = list(answers)
        self.sent: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.sent.append(request)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.fixture
def waits(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    slept: list[float] = []
    monkeypatch.setattr("time.sleep", slept.append)
    return slept


def client(net: Network, **kw: Any) -> Steward:
    http = httpx.Client(transport=httpx.MockTransport(net), base_url="http://steward")
    return Steward(api_key="stw_k_s", http_client=http, **kw)


def test_a_response_is_the_apis_own_model(waits: list[float]) -> None:
    net = Network(httpx.Response(201, json=MEMORY))
    memory = client(net).memories.create("likes green tea", user_id="u1")
    assert isinstance(memory, models.MemoryOut) and memory.content == "likes green tea"
    [sent] = net.sent
    assert sent.headers["Authorization"] == "Bearer stw_k_s"
    assert sent.read() == b'{"content":"likes green tea","user_id":"u1"}'


def test_a_shared_http_client_keeps_each_clients_own_key(waits: list[float]) -> None:
    """Two clients on one httpx.Client (e.g. a host key and a reviewer key) never overwrite each
    other's credential: it goes on each request, not into the shared client."""
    net = Network(*(httpx.Response(200, json=MEMORY) for _ in range(2)))
    http = httpx.Client(transport=httpx.MockTransport(net), base_url="http://steward")
    host, reviewer = (
        Steward(api_key="stw_a_host", http_client=http),
        Steward(api_key="stw_b_reviewer", http_client=http),
    )
    host.memories.get("x")
    reviewer.memories.get("x")
    assert [r.headers["Authorization"] for r in net.sent] == [
        "Bearer stw_a_host",
        "Bearer stw_b_reviewer",
    ]
    assert "Authorization" not in http.headers


@pytest.mark.parametrize(
    ("status", "code", "kind"),
    [
        (404, "not_found", NotFoundError),
        (409, "conflict", ConflictError),
        (422, "validation_error", UnprocessableError),
        (400, "invalid_idempotency_key", StewardError),
    ],
)
def test_problems_become_typed_errors(
    waits: list[float], status: int, code: str, kind: type[Exception]
) -> None:
    with pytest.raises(kind) as raised:
        client(Network(problem(status, code, "what went wrong"))).memories.get("x")
    error: Any = raised.value
    assert (error.status, error.code, error.detail) == (status, code, "what went wrong")


@pytest.mark.parametrize(
    ("status", "kind"), [(401, AuthenticationError), (403, PermissionDeniedError)]
)
def test_credential_problems_are_never_retried(
    waits: list[float], status: int, kind: type[Exception]
) -> None:
    net = Network(problem(status, "unauthenticated"), httpx.Response(201, json=MEMORY))
    with pytest.raises(kind):
        client(net).memories.create("x")
    assert len(net.sent) == 1 and waits == []


def test_a_busy_server_is_retried_with_the_same_idempotency_key(waits: list[float]) -> None:
    net = Network(problem(503, "provider_unavailable"), httpx.Response(201, json=MEMORY))
    client(net).memories.create("likes green tea")
    first, second = net.sent
    assert first.headers["Idempotency-Key"] == second.headers["Idempotency-Key"]
    uuid.UUID(first.headers["Idempotency-Key"])  # a fresh UUID per write
    assert waits == [0.5]


def test_each_write_gets_its_own_key_and_reads_none(waits: list[float]) -> None:
    net = Network(*(httpx.Response(201, json=MEMORY) for _ in range(2)))
    st = client(net)
    st.memories.create("a")
    st.memories.create("b")
    keys = {r.headers["Idempotency-Key"] for r in net.sent}
    assert len(keys) == 2
    read = Network(httpx.Response(200, json=MEMORY))
    client(read).memories.get("x")
    assert "Idempotency-Key" not in read.sent[0].headers


def test_throttling_honours_retry_after_within_a_cap(waits: list[float]) -> None:
    net = Network(
        problem(429, "rate_limited", **{"Retry-After": "7"}),
        problem(429, "rate_limited", **{"Retry-After": "9999"}),
        httpx.Response(200, json=MEMORY),
    )
    client(net).memories.get("x")
    assert waits == [7.0, 30.0]


def test_after_the_retries_the_last_answer_is_raised(waits: list[float]) -> None:
    net = Network(*(problem(429, "rate_limited", **{"Retry-After": "2"}) for _ in range(3)))
    with pytest.raises(RateLimitError) as raised:
        client(net, max_retries=2).memories.get("x")
    assert raised.value.retry_after == 2.0 and len(net.sent) == 3
    net = Network(*(problem(502, "http_error") for _ in range(2)))
    with pytest.raises(ServerError):
        client(net, max_retries=1).memories.get("x")


def test_network_errors_are_retried_then_raised(waits: list[float]) -> None:
    down = httpx.ConnectError("refused")
    net = Network(down, httpx.Response(200, json=MEMORY))
    assert client(net).memories.get("x").content == "likes green tea"
    with pytest.raises(StewardConnectionError):
        client(Network(down, down), max_retries=1).memories.get("x")


def test_unset_options_are_not_sent(waits: list[float]) -> None:
    page: dict[str, Any] = {"items": [], "next_cursor": None}
    net = Network(httpx.Response(200, json=page))
    client(net).memories.list(user_id="u1", tag=None)
    assert str(net.sent[0].url) == "http://steward/v1/memories?user_id=u1"


def test_a_body_less_answer_is_none(waits: list[float]) -> None:
    net = Network(httpx.Response(204))
    client(net).feedback("r", "i", helpful=True)  # returns None; nothing to parse
    assert net.sent[0].read() == b'{"item_id":"i","helpful":true}'


# --- async: the same rules ----------------------------------------------------------------------


def aclient(net: Network) -> AsyncSteward:
    http = httpx.AsyncClient(transport=httpx.MockTransport(net), base_url="http://steward")
    return AsyncSteward(api_key="stw_k_s", http_client=http)


def _run(make: Callable[[], Any]) -> Any:
    return asyncio.run(make())


def test_async_retries_and_keys_like_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []

    async def no_wait(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr("asyncio.sleep", no_wait)
    net = Network(problem(503, "provider_unavailable"), httpx.Response(201, json=MEMORY))
    memory = _run(lambda: aclient(net).memories.create("likes green tea"))
    assert isinstance(memory, models.MemoryOut)
    assert net.sent[0].headers["Idempotency-Key"] == net.sent[1].headers["Idempotency-Key"]
    assert slept == [0.5]
    one = Network(problem(401, "unauthenticated"))
    with pytest.raises(AuthenticationError):
        _run(lambda: aclient(one).memories.get("x"))
    assert len(one.sent) == 1
