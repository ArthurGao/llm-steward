"""One request, sync or async: the Idempotency-Key, retries and errors live here.

- Every write (POST, PATCH, DELETE) carries an `Idempotency-Key`, the same one across its
  retries, so a retried write is applied once (the server replays the first answer).
- Retried: 408, 429 (honouring `Retry-After`), 5xx and network errors, `max_retries` times with
  backoff. Never retried: 401 and 403 (a credential problem a retry cannot fix) or other 4xx.
"""

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from llm_steward_client._errors import RateLimitError, StewardConnectionError, error_for

WRITES = frozenset({"POST", "PATCH", "PUT", "DELETE"})
RETRY_STATUSES = frozenset({408, 429, 500, 502, 503, 504})
MAX_RETRY_AFTER_S = 30.0


@dataclass(frozen=True)
class Call[T]:
    method: str
    path: str
    json: Any = None
    params: dict[str, Any] | None = None
    data: dict[str, Any] | None = None
    files: dict[str, Any] | None = None
    model: Any = None  # the response body's model (a pydantic model, T); None: no body (204)
    headers: dict[str, str] = field(default_factory=dict)

    def request_kwargs(
        self, idempotency_key: str | None, headers: dict[str, str] | None = None
    ) -> dict[str, Any]:
        headers = {**(headers or {}), **self.headers}
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        return {
            "method": self.method,
            "url": self.path,
            "json": self.json,
            "params": self.params or None,  # None values already left out by the builders
            "data": self.data,
            "files": self.files,
            "headers": headers,
        }


def _key(call: Call[Any]) -> str | None:
    return str(uuid.uuid4()) if call.method in WRITES else None


def _wait(response: httpx.Response | None, attempt: int) -> float:
    if response is not None:
        hint = response.headers.get("Retry-After")
        if hint is not None:
            try:
                return min(max(float(hint), 0.0), MAX_RETRY_AFTER_S)
            except ValueError:
                pass
    return min(0.5 * 2.0**attempt, 8.0)


def _result[T](call: Call[T], response: httpx.Response) -> T:
    if response.is_success:
        if call.model is None or response.status_code == 204:
            return None  # type: ignore[return-value]  # Call[None]: a body-less answer
        parsed: T = call.model.model_validate_json(response.content)
        return parsed
    try:
        body = response.json()
    except ValueError:
        body = None
    error = error_for(response.status_code, body)
    if isinstance(error, RateLimitError):
        error.retry_after = _wait(response, 0)
    raise error


def execute[T](
    http: httpx.Client,
    call: Call[T],
    *,
    max_retries: int,
    headers: dict[str, str] | None = None,
    sleep: Callable[[float], None] | None = None,
) -> T:
    sleep = sleep or time.sleep  # looked up per call (tests replace it)
    key = _key(call)
    for attempt in range(max_retries + 1):
        last = attempt == max_retries
        try:
            response = http.request(**call.request_kwargs(key, headers))
        except httpx.TransportError as exc:
            if last:
                raise StewardConnectionError(0, "connection_error", str(exc)) from exc
            sleep(_wait(None, attempt))
            continue
        if response.status_code in RETRY_STATUSES and not last:
            sleep(_wait(response, attempt))
            continue
        return _result(call, response)
    raise AssertionError("unreachable")  # pragma: no cover


async def aexecute[T](
    http: httpx.AsyncClient,
    call: Call[T],
    *,
    max_retries: int,
    headers: dict[str, str] | None = None,
    sleep: Callable[[float], Awaitable[None]] | None = None,
) -> T:
    sleep = sleep or asyncio.sleep
    key = _key(call)
    for attempt in range(max_retries + 1):
        last = attempt == max_retries
        try:
            response = await http.request(**call.request_kwargs(key, headers))
        except httpx.TransportError as exc:
            if last:
                raise StewardConnectionError(0, "connection_error", str(exc)) from exc
            await sleep(_wait(None, attempt))
            continue
        if response.status_code in RETRY_STATUSES and not last:
            await sleep(_wait(response, attempt))
            continue
        return _result(call, response)
    raise AssertionError("unreachable")  # pragma: no cover
