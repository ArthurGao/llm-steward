"""The one place that maps configured provider names to instances.

Everything else talks to providers through their Protocol, never by name (backend/AGENTS.md).
"""

import httpx2
from sqlalchemy.ext.asyncio import AsyncEngine

from llm_steward.config import Settings
from llm_steward.providers.base import (
    KnowledgeProvider,
    MemoryProvider,
    PendingKnowledgeProvider,
    PendingMemoryProvider,
    Providers,
)
from llm_steward.providers.native.embedding import Embedder, FastEmbedEmbedder
from llm_steward.providers.weknora import WeKnoraProvider


def uses_native(settings: Settings) -> bool:
    return "native" in (settings.memory_provider, settings.kb_provider)


def build_embedder(settings: Settings) -> Embedder:
    return FastEmbedEmbedder(settings.embedding_model, settings.model_cache_dir)


def build_providers(
    settings: Settings,
    http: httpx2.AsyncClient,
    engine: AsyncEngine,
    embedder: Embedder | None,
) -> Providers:
    """`embedder` is required when a native provider is configured (see `uses_native`)."""
    store = None
    if uses_native(settings):
        if embedder is None:
            raise ValueError("a native provider is configured but no embedder was given")
        from llm_steward.providers.native.providers import create_store

        store = create_store(engine, embedder, settings.db_schema)

    memory: MemoryProvider
    match settings.memory_provider:
        case "native":
            from llm_steward.providers.native.providers import NativeMemoryProvider

            assert store is not None and embedder is not None
            memory = NativeMemoryProvider(store, embedder)
        case "mem0":
            memory = PendingMemoryProvider("mem0", "P2")

    kb: KnowledgeProvider
    match settings.kb_provider:
        case "native":
            from llm_steward.providers.native.providers import NativeKnowledgeProvider

            assert store is not None and embedder is not None
            kb = NativeKnowledgeProvider(store, embedder)
        case "weknora":
            kb = WeKnoraProvider(http, settings.weknora_url, settings.provider_timeout_ms / 1000)
        case "ragflow":
            kb = PendingKnowledgeProvider("ragflow", "P7")

    return Providers(memory=memory, kb=kb)
