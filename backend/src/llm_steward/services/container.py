"""Wires one runtime: engine, HTTP client, embedder, providers and services.

`runtime()` is the single place both entry points (the API lifespan and `steward worker`) build
from, so they can never drift apart in how providers are configured.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx2
from sqlalchemy.ext.asyncio import AsyncEngine

from llm_steward.config import Settings
from llm_steward.providers.base import Providers
from llm_steward.providers.native.embedding import Embedder
from llm_steward.providers.registry import build_embedder, build_providers, uses_native
from llm_steward.services.governance_service import GovernanceService
from llm_steward.services.job_queue import JobQueue
from llm_steward.services.memory_service import MemoryService
from llm_steward.services.namespace_service import NamespaceService
from llm_steward.services.retrieval_service import RetrievalService
from llm_steward.services.sync_service import SyncService
from llm_steward.services.worker import Worker
from llm_steward.storage.db import engine_scope, session_factory


@dataclass(frozen=True)
class Services:
    engine: AsyncEngine
    providers: Providers
    embedder: Embedder | None
    governance: GovernanceService
    namespaces: NamespaceService
    memories: MemoryService
    retrieval: RetrievalService
    worker: Worker


def build_services(
    settings: Settings, engine: AsyncEngine, providers: Providers, embedder: Embedder | None
) -> Services:
    sessions = session_factory(engine)
    governance = GovernanceService(
        sessions, memory_provider=providers.memory.name, kb_provider=providers.kb.name
    )
    return Services(
        engine=engine,
        providers=providers,
        embedder=embedder,
        governance=governance,
        namespaces=NamespaceService(sessions),
        memories=MemoryService(sessions, governance, providers.memory),
        retrieval=RetrievalService(
            sessions, providers, timeout_s=settings.retrieve_timeout_ms / 1000
        ),
        worker=Worker(JobQueue(sessions), SyncService(sessions, providers)),
    )


@asynccontextmanager
async def runtime(
    settings: Settings,
    *,
    http: httpx2.AsyncClient | None = None,
    embedder: Embedder | None = None,
) -> AsyncIterator[Services]:
    """`http` and `embedder` are injectable (tests stub the network and the model); whatever is
    created here is also closed here."""
    async with engine_scope(settings.database_url, schema=settings.db_schema) as engine:
        client = http or httpx2.AsyncClient()
        try:
            embed = embedder or (build_embedder(settings) if uses_native(settings) else None)
            providers = build_providers(settings, client, engine, embed)
            yield build_services(settings, engine, providers, embed)
        finally:
            if http is None:
                await client.aclose()
