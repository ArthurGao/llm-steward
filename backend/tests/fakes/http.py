"""An HTTP client whose requests go to a handler (httpx2.MockTransport): the network boundary."""

import json
from collections.abc import Callable
from typing import Any, ClassVar

import httpx2

Handler = Callable[[httpx2.Request], httpx2.Response]


def mock_http(handler: Callable[[httpx2.Request], httpx2.Response]) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(transport=httpx2.MockTransport(handler))


class Router:
    """A scripted engine API: routes (method, path) to a handler and records every request. A
    route it does not know gets HTTP 404 with the engine's own error body (`not_found`)."""

    not_found: ClassVar[dict[str, Any]] = {}

    def __init__(self, routes: dict[tuple[str, str], Handler]):
        self.routes = routes
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        handler = self.routes.get((request.method, request.url.path))
        if handler is None:
            return httpx2.Response(404, json=self.not_found)
        return handler(request)

    def calls(self) -> list[str]:
        return [f"{r.method} {r.url.path}" for r in self.requests]

    def body(self, method: str, path: str) -> dict[str, Any]:
        [request] = [r for r in self.requests if (r.method, r.url.path) == (method, path)]
        return json.loads(request.content)  # type: ignore[no-any-return]
