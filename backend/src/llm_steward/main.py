import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx2
from fastapi import FastAPI

from llm_steward.api import errors, system
from llm_steward.api import v1 as api_v1
from llm_steward.config import Settings
from llm_steward.llm.client import LLM
from llm_steward.providers.base import MemoryProvider
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
) -> FastAPI:
    """Build the app. `http`, `embedder`, `memory_provider` and `llm` are injectable for tests."""
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with runtime(
            settings, http=http, embedder=embedder, memory_provider=memory_provider, llm=llm
        ) as services:
            app.state.services = services
            stop = asyncio.Event()
            background: list[asyncio.Task[None]] = []
            if services.embedder is not None and embedder is None:
                background.append(asyncio.create_task(_warm_up(services.embedder)))
            if settings.run_worker:
                background.append(asyncio.create_task(services.worker.run_forever(stop)))
            try:
                yield
            finally:
                stop.set()
                for task in background:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task

    app = FastAPI(title="llm-steward", version="0.1.0", lifespan=lifespan)
    errors.install(app)
    app.include_router(system.router)
    app.include_router(api_v1.router)
    return app


async def _warm_up(embedder: Embedder) -> None:
    """Load (and on first run download) the local model in the background, so the first retrieve
    is not the one that pays for it."""
    try:
        await embedder.embed(["warm-up"])
    except Exception:
        log.exception("embedding model warm-up failed; the first request will retry it")
