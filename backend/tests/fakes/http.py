"""An HTTP client whose requests go to a handler (httpx2.MockTransport): the network boundary."""

from collections.abc import Callable

import httpx2


def mock_http(handler: Callable[[httpx2.Request], httpx2.Response]) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
