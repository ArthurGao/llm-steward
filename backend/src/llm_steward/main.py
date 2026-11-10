import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Coroutine
from contextlib import asynccontextmanager
from typing import Any

import httpx2
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from mcp.server.transport_security import TransportSecuritySettings

from llm_steward.api import admin as api_admin
from llm_steward.api import errors, system
from llm_steward.api import v1 as api_v1
from llm_steward.api.idempotency import IdempotencyMiddleware
from llm_steward.api.mcp_http import McpAuth
from llm_steward.config import Settings
from llm_steward.llm.client import LLM
from llm_steward.mcp.server import build_server as build_mcp_server
from llm_steward.providers.base import MemoryProvider, Reranker
from llm_steward.providers.native.embedding import Embedder
from llm_steward.services.container import runtime

log = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    *,
    http: httpx2.AsyncClient | None = None,
    embedder: Embedder | None = None,
    memory_provider: MemoryProvider | None = None,
    llm: LLM | None = None,
    reranker: Reranker | None = None,
) -> FastAPI:
    """Build the app. `http`, `embedder`, `memory_provider`, `llm` and `reranker` are injectable
    for tests."""
    settings = settings or Settings()
    # MCP over streamable HTTP (design §7): stateless JSON, so any API process can answer any
    # call. Its DNS-rebinding check (Host must be localhost) is off: every request needs a bearer
    # credential, which a page rebinding DNS does not have, and llm-steward runs behind real hosts.
    mcp_server = build_mcp_server(lambda: app.state.services)
    mcp_app = mcp_server.streamable_http_app(
        streamable_http_path="/",
        json_response=True,
        stateless_http=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with runtime(
            settings,
            http=http,
            embedder=embedder,
            memory_provider=memory_provider,
            llm=llm,
            reranker=reranker,
        ) as services:
            app.state.services = services
            stop = asyncio.Event()
            background: list[asyncio.Task[None]] = []
            if services.embedder is not None and embedder is None:
                embed = services.embedder.embed(["warm-up"])
                background.append(asyncio.create_task(_warm_up("embedding", embed)))
            if services.reranker is not None and reranker is None:
                score = services.reranker.score("warm-up", ["warm-up"])
                background.append(asyncio.create_task(_warm_up("rerank", score)))
            if settings.run_worker:
                background.append(asyncio.create_task(services.worker.run_forever(stop)))
            try:
                async with mcp_server.session_manager.run():  # a mounted app's lifespan never runs
                    yield
            finally:
                stop.set()
                for task in background:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task

    app = FastAPI(title="llm-steward", version="0.1.0", lifespan=lifespan)
    errors.install(app)
    # an upload's limit, plus room for the multipart framing
    app.add_middleware(IdempotencyMiddleware, max_body=(settings.kb_max_upload_mb + 1) * 2**20)
    if settings.cors_origins:  # outermost: preflights are answered before auth
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
            allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
            expose_headers=["Idempotent-Replayed", "Retry-After"],
        )
    app.include_router(system.router)
    app.include_router(api_v1.router)
    app.include_router(api_admin.router)
    app.mount("/mcp", McpAuth(mcp_app))
    return app


async def _warm_up(name: str, call: Coroutine[Any, Any, Any]) -> None:
    """Load (and on first run download) a local model in the background, so the first retrieve
    is not the one that pays for it."""
    try:
        await call
    except Exception:
        log.exception("%s model warm-up failed; the first request will retry it", name)
