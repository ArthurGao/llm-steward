from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from llm_steward.api import system
from llm_steward.config import Settings
from llm_steward.providers.registry import build_providers
from llm_steward.storage.db import create_engine


def create_app(
    settings: Settings | None = None, *, http: httpx.AsyncClient | None = None
) -> FastAPI:
    """Build the app. `http` is injectable so tests can stub provider endpoints."""
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings.database_url, schema=settings.db_schema)
        client = http or httpx.AsyncClient()
        app.state.engine = engine
        app.state.providers = build_providers(settings, client)
        try:
            yield
        finally:
            if http is None:
                await client.aclose()
            await engine.dispose()

    app = FastAPI(title="llm-steward", version="0.1.0", lifespan=lifespan)
    app.include_router(system.router)
    return app
