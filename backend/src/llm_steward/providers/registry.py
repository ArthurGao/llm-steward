"""The one place that maps configured provider names to instances.

Everything else talks to providers through their Protocol, never by name (backend/AGENTS.md).
"""

from pathlib import Path

import httpx2
from sqlalchemy.ext.asyncio import AsyncEngine

from llm_steward.config import Settings
from llm_steward.llm.roles import Role, resolve
from llm_steward.providers.base import (
    KnowledgeProvider,
    MemoryProvider,
    PendingKnowledgeProvider,
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
    *,
    memory: MemoryProvider | None = None,
) -> Providers:
    """`embedder` is required when a native provider is configured (see `uses_native`). A given
    `memory` provider (tests) replaces the configured one, which is then not built at all."""
    store = None
    if uses_native(settings):
        if embedder is None:
            raise ValueError("a native provider is configured but no embedder was given")
        from llm_steward.providers.native.providers import create_store

        store = create_store(engine, embedder, settings.db_schema)

    if memory is None:
        match settings.memory_provider:
            case "native":
                from llm_steward.providers.native.providers import NativeMemoryProvider

                assert store is not None and embedder is not None
                memory = NativeMemoryProvider(store, embedder)
            case "mem0":
                memory = build_mem0(settings)

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


def build_mem0(settings: Settings) -> MemoryProvider:
    """mem0 on the governance PostgreSQL (schema mem0), local MiniLM embeddings, extraction through
    the `memory.extract` role (fails here, at startup, when that role cannot be served)."""
    from llm_steward.providers.mem0_provider import Mem0Provider, build_mem0_config
    from llm_steward.providers.native.embedding import model_dim

    extract = resolve(settings, Role.MEMORY_EXTRACT)
    config = build_mem0_config(
        embedding_model=settings.embedding_model,
        embedding_dims=model_dim(settings.embedding_model),
        llm_model=extract.model,
        llm_base_url=extract.base_url,
        llm_api_key=extract.api_key,
        history_db_path=str(Path(settings.data_dir) / "mem0_history.db"),
    )
    return Mem0Provider(
        config, database_url=settings.database_url, model_cache_dir=settings.model_cache_dir
    )
