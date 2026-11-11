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
    Providers,
    Reranker,
)
from llm_steward.providers.native.embedding import Embedder, FastEmbedEmbedder
from llm_steward.providers.ragflow import RAGFlowProvider
from llm_steward.providers.weknora import WeKnoraProvider


def uses_native(settings: Settings) -> bool:
    return "native" in (settings.memory_provider, settings.kb_provider)


def build_embedder(settings: Settings) -> Embedder:
    return FastEmbedEmbedder(settings.embedding_model, settings.model_cache_dir)


def build_reranker(settings: Settings) -> Reranker | None:
    if settings.rerank_model is None:
        return None
    from llm_steward.providers.rerank import FastEmbedReranker

    return FastEmbedReranker(settings.rerank_model, settings.model_cache_dir)


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
            case "litemem":
                memory = build_litemem(settings, http)

    kb: KnowledgeProvider
    match settings.kb_provider:
        case "native":
            from llm_steward.providers.native.providers import NativeKnowledgeProvider

            assert store is not None and embedder is not None
            kb = NativeKnowledgeProvider(store, embedder)
        case "weknora":
            kb = WeKnoraProvider(
                http,
                settings.weknora_url,
                settings.weknora_api_key,
                settings.weknora_embedding_model_id,
                timeout_s=settings.provider_timeout_ms / 1000,
            )
        case "ragflow":
            kb = RAGFlowProvider(
                http,
                settings.ragflow_url,
                settings.ragflow_api_key,
                timeout_s=settings.provider_timeout_ms / 1000,
                parser_id=settings.ragflow_parser_id,
            )
        case "literag":
            kb = build_literag(settings, http)

    return Providers(memory=memory, kb=kb)


# In-process lite-mem calls at once; its store pool has as many connections (lite-mem factory).
LITEMEM_CONCURRENCY = 4


def build_litemem(settings: Settings, http: httpx2.AsyncClient) -> MemoryProvider:
    """lite-mem over its HTTP API (default), or in-process: its components (store, embedding
    model, LLM clients) are built here, once, from lite-mem's own configuration (and fail here,
    at startup, when that is wrong)."""
    from llm_steward.providers.litemem import (
        HttpTransport,
        InProcessTransport,
        LiteMemProvider,
        Transport,
    )

    transport: Transport
    if settings.litemem_mode == "inprocess":
        from lite_mem.config import Settings as LiteMemSettings
        from lite_mem.factory import Engines, open_components

        own = LiteMemSettings(_env_file=settings.litemem_config)
        components = open_components(own, max_size=LITEMEM_CONCURRENCY)
        engines = Engines(components, max_namespaces=own.server.max_namespaces)
        transport = InProcessTransport(components, engines, LITEMEM_CONCURRENCY)
    else:
        transport = HttpTransport(
            http, settings.litemem_url, settings.litemem_api_key, settings.litemem_timeout_s
        )
    return LiteMemProvider(transport, settings.litemem_namespace_prefix)


# In-process lite-rag calls at once; its store pool has as many connections (lite-rag design §3).
LITERAG_CONCURRENCY = 4


def build_literag(settings: Settings, http: httpx2.AsyncClient) -> KnowledgeProvider:
    """lite-rag over its HTTP API (default), or in-process: its runtime is built here, once, from
    lite-rag's own configuration (and fails here, at startup, when that is wrong)."""
    from llm_steward.providers.literag import (
        HttpTransport,
        InProcessTransport,
        LiteRagProvider,
        Transport,
    )

    transport: Transport
    if settings.literag_mode == "inprocess":
        from lite_rag.config import load_settings
        from lite_rag.engine import RagRuntime

        runtime = RagRuntime.from_settings(
            load_settings(settings.literag_config), max_connections=LITERAG_CONCURRENCY
        )
        transport = InProcessTransport(
            runtime,
            LITERAG_CONCURRENCY,
            mode=settings.literag_search_mode,
            rerank=settings.literag_rerank,
        )
    else:
        timeout = settings.provider_timeout_ms / 1000
        transport = HttpTransport(
            http,
            settings.literag_url,
            settings.literag_api_key,
            timeout,
            mode=settings.literag_search_mode,
            rerank=settings.literag_rerank,
        )
    return LiteRagProvider(transport, settings.literag_namespace_prefix)


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
