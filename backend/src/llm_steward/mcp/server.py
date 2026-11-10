"""MCP server (design §7): six tools for agents, on the same services and the same auth as REST.

Over HTTP (`/mcp`, streamable HTTP, stateless JSON) the caller is resolved by the API layer's
auth step before the MCP app runs, exactly like a REST call (API key or user token, rate limit),
and handed to the tools through the request. Over stdio (`steward mcp --stdio`) there is no
request: the server is built for one principal, authenticated from STEWARD_API_KEY at start.

Rule 5: an agent may only *propose* knowledge. Every tool acts as its caller marked
`propose_only` (governance then sends any knowledge write to the review queue and refuses any
decision, whatever the key's review right), and nothing here names a knowledge write or a
decision (a test pins the names this package may use).
"""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from llm_steward.errors import StewardError
from llm_steward.models.enums import ApiScope
from llm_steward.providers.base import Scope
from llm_steward.services.container import Services
from llm_steward.services.governance_service import Actor, CreateMemory
from llm_steward.services.namespace_service import PRINCIPAL_STATE_KEY, Principal
from llm_steward.services.retrieval_service import RetrieveRequest

TOOLS = ("remember", "recall", "forget", "suggest_kb", "search_kb", "get_profile")
NOTHING = "Nothing relevant is stored."

INSTRUCTIONS = (
    "llm-steward keeps governed memory and team knowledge. Use `recall` before answering about "
    "the user, `remember` for a durable fact the user stated, `forget` when they ask, "
    "`search_kb` for team knowledge, and `suggest_kb` to propose knowledge: a person reviews it "
    "before anyone else sees it."
)


def build_server(
    services: Callable[[], Services], *, principal: Principal | None = None
) -> MCPServer:
    """`services` is read per call (the app builds them in its lifespan). `principal`: the one
    caller of a stdio server; None over HTTP, where each request carries its own."""
    server = MCPServer(name="llm-steward", instructions=INSTRUCTIONS)

    def caller(ctx: Context[Any, Any], needs: ApiScope) -> Principal:
        found = principal
        if found is None:
            request = ctx.request_context.request  # the HTTP request this call came in on
            found = getattr(getattr(request, "state", None), PRINCIPAL_STATE_KEY, None)
        if found is None:
            raise ToolError("not authenticated")
        if needs not in found.scopes:
            raise ToolError(f"this key lacks the {needs.value!r} scope")
        return found

    @server.tool(description="Store one durable fact about the user (or a shared one).")
    async def remember(
        fact: str,
        ctx: Context[Any, Any],
        user_id: str | None = None,
        memory_type: str = "semantic",
        importance: float = 0.5,
    ) -> dict[str, Any]:
        p = caller(ctx, ApiScope.WRITE)
        op = CreateMemory(
            namespace_id=p.namespace_id,
            content=fact,
            user_id=_pin(p, user_id),
            memory_type=memory_type,
            importance=importance,
        )
        view = await _run(services().memories.create(op, infer=False, actor=_proposer(p)))
        return {"id": str(view.memory.id), "user_id": view.memory.user_id, "status": "stored"}

    @server.tool(description="Recall what is known that is relevant to a query, as context text.")
    async def recall(
        query: str, ctx: Context[Any, Any], user_id: str | None = None, top_k: int = 8
    ) -> str:
        p = caller(ctx, ApiScope.READ)
        req = RetrieveRequest(query, user_id=_pin(p, user_id), top_k=top_k, with_context=True)
        result = await _run(services().retrieval.retrieve(p.namespace_id, req))
        return result.context or NOTHING

    @server.tool(description="Forget one memory by its id (it stops being recalled).")
    async def forget(memory_id: str, ctx: Context[Any, Any]) -> dict[str, Any]:
        p = caller(ctx, ApiScope.WRITE)
        view = await _run(
            services().memories.delete(
                p.namespace_id, _uuid(memory_id), actor=_proposer(p), owner=p.user_id
            )
        )
        return {"id": memory_id, "status": view.memory.status.value}

    @server.tool(
        description="Propose saving text to the team knowledge base. A reviewer decides; it is "
        "never live at once."
    )
    async def suggest_kb(
        text: str, collection_id: str, ctx: Context[Any, Any], user_id: str | None = None
    ) -> dict[str, Any]:
        p = caller(ctx, ApiScope.WRITE)
        submitted = await _run(
            services().kb.suggest(
                p.namespace_id,
                _uuid(collection_id),
                text,
                actor=_proposer(p),
                user_id=_pin(p, user_id),
            )
        )
        candidate = submitted.candidate
        assert candidate is not None  # a proposer's write is always a candidate
        return {"candidate_id": str(candidate.id), "stage": candidate.stage.value}

    @server.tool(description="Search the team knowledge base only, as context text.")
    async def search_kb(
        query: str,
        ctx: Context[Any, Any],
        collections: list[str] | None = None,
        top_k: int = 8,
    ) -> str:
        p = caller(ctx, ApiScope.READ)
        req = RetrieveRequest(
            query,
            sources=("kb",),
            collections=tuple(collections) if collections else None,
            top_k=top_k,
            with_context=True,
        )
        result = await _run(services().retrieval.retrieve(p.namespace_id, req))
        return result.context or NOTHING

    @server.tool(description="The user's profile: current key-value facts and pinned memories.")
    async def get_profile(ctx: Context[Any, Any], user_id: str | None = None) -> dict[str, Any]:
        p = caller(ctx, ApiScope.READ)
        user = _pin(p, user_id)
        if user is None:
            raise ToolError("user_id is required with an API key")
        profile = await _run(services().memories.profile(Scope(p.namespace_id, user)))
        return {
            "user_id": user,
            "values": profile.values,
            "pinned": [m.content for m in profile.pinned],
        }

    return server


def _proposer(principal: Principal) -> Actor:
    """The caller, who may only propose: an agent's key may carry `review`, its tools may not."""
    return replace(principal.actor, can_review=False, propose_only=True)


def _pin(principal: Principal, user_id: str | None) -> str | None:
    try:
        return principal.pin_user(user_id)
    except StewardError as exc:  # a user token naming another user
        raise ToolError(exc.detail) from exc


def _uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise ToolError(f"not an id: {value!r}") from exc


async def _run[T](call: Awaitable[T]) -> T:
    """A domain error becomes the tool's error text (the agent reads it); others propagate."""
    try:
        return await call
    except StewardError as exc:
        raise ToolError(exc.detail) from exc
