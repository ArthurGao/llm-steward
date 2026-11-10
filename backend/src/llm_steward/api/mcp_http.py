"""`/mcp` over HTTP: the MCP app behind the same auth step as every REST route (design §7).

The caller is resolved (API key or user token, charged to its rate limit) before the MCP app
sees the request; 401 and 429 answer as Problem Details, exactly as on `/v1`. The tools read the
resolved principal from the request (`mcp/server.py`).
"""

from typing import Any

from fastapi import HTTPException
from starlette.types import ASGIApp, Receive, Scope, Send

from llm_steward.api.deps import principal_from_scope
from llm_steward.api.errors import http_problem


class McpAuth:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        services: Any = scope["app"].state.services
        try:
            await principal_from_scope(services, scope)
        except HTTPException as exc:
            await http_problem(exc)(scope, receive, send)
            return
        await self.app(scope, receive, send)
