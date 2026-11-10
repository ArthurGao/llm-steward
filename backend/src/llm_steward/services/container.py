"""Wires one runtime: engine, HTTP client, embedder, providers, LLM roles and services.

`runtime()` is the single place both entry points (the API lifespan and `steward worker`) build
from, so they can never drift apart in how providers are configured. A configuration that cannot
serve a needed LLM role fails here, at startup (KB llm-app-architecture §1.5).
"""

import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import httpx2
from sqlalchemy.ext.asyncio import AsyncEngine

from llm_steward.config import Settings
from llm_steward.llm.client import LLM, OpenAICompatibleLLM
from llm_steward.llm.roles import Role
from llm_steward.models.enums import JobType
from llm_steward.namespace_config import read_config
from llm_steward.outbound import Outbound
from llm_steward.providers.base import MemoryProvider, Providers, Reranker
from llm_steward.providers.native.embedding import Embedder
from llm_steward.providers.registry import (
    build_embedder,
    build_providers,
    build_reranker,
    uses_native,
)
from llm_steward.services.admin_service import AdminService
from llm_steward.services.document_service import DocumentService
from llm_steward.services.episode_service import EpisodeService
from llm_steward.services.extraction_service import ExtractionService, compensates
from llm_steward.services.governance_service import GovernanceService
from llm_steward.services.idempotency_service import IdempotencyService
from llm_steward.services.job_queue import ClaimedJob, JobQueue
from llm_steward.services.kb_service import KbService
from llm_steward.services.memory_service import MemoryService
from llm_steward.services.namespace_service import NamespaceService
from llm_steward.services.rate_limit import RateLimiter
from llm_steward.services.reconcile_service import ReconcileService, schedule_reconcile
from llm_steward.services.retrieval_service import RetrievalService
from llm_steward.services.sync_service import SyncService
from llm_steward.services.webhook_service import WebhookService
from llm_steward.services.worker import Tick, Worker
from llm_steward.storage.db import engine_scope, session_factory

Handler = Callable[[ClaimedJob], Awaitable[Any]]

log = logging.getLogger(__name__)
IDLE_FLUSH_EVERY_S = 60.0
EXPIRE_EVERY_S = 24 * 3600.0  # candidates expire after days (governance CANDIDATE_TTL)
HITS_EVERY_S = 30.0  # hit counts lag retrievals by at most this (design §6 step 9)
PURGE_IDEMPOTENCY_EVERY_S = 3600.0  # keys are kept a day; an hour late is harmless


@dataclass(frozen=True)
class Services:
    engine: AsyncEngine
    providers: Providers
    embedder: Embedder | None
    reranker: Reranker | None
    governance: GovernanceService
    namespaces: NamespaceService
    memories: MemoryService
    kb: KbService
    documents: DocumentService
    episodes: EpisodeService
    reconcile: ReconcileService
    retrieval: RetrievalService
    worker: Worker
    jobs: JobQueue
    idempotency: IdempotencyService
    rate_limiter: RateLimiter
    webhooks: WebhookService
    outbound: Outbound
    admin: AdminService
    admin_token: str | None  # STEWARD_ADMIN_TOKEN; None: the Admin API is off
    embedding_model: str | None  # what the native engine embeds with (the Admin shows it)


def build_llm(settings: Settings, providers: Providers) -> LLM | None:
    """Exactly the roles that will be used, resolved now (a missing model fails at startup):
    `memory.resolve` when conflict compensation runs, the KB roles when `kb_llm` is on."""
    roles = [Role.MEMORY_RESOLVE] if compensates(providers.memory) else []
    if settings.kb_llm:
        roles += [Role.KB_RISK, Role.KB_WORTHINESS, Role.KB_DRAFT]
    else:
        log.info("KB LLM steps are off (STEWARD_KB_LLM): rules-only risk, no suggestions")
    return OpenAICompatibleLLM(settings, tuple(roles)) if roles else None


def build_services(
    settings: Settings,
    engine: AsyncEngine,
    providers: Providers,
    embedder: Embedder | None,
    llm: LLM | None,
    *,
    reranker: Reranker | None,
) -> Services:
    sessions = session_factory(engine)
    governance = GovernanceService(
        sessions, memory_provider=providers.memory.name, kb_provider=providers.kb.name
    )
    sync = SyncService(sessions, providers)
    kb = KbService(sessions, governance, llm if settings.kb_llm else None)
    extraction = ExtractionService(
        sessions, governance, providers.memory, llm, on_extracted=kb.queue_suggestions
    )
    reconcile = ReconcileService(sessions, providers.memory)
    idempotency = IdempotencyService(sessions)
    out = Outbound(allow_private=settings.allow_private_urls)  # user-supplied URLs only
    webhooks = WebhookService(sessions, out)
    documents = DocumentService(
        sessions,
        governance,
        providers.kb,
        max_bytes=settings.kb_max_upload_mb * 1024 * 1024,
        outbound=out,
    )

    async def namespace_rpm(namespace_id: uuid.UUID) -> int | None:
        async with sessions() as s:
            return (await read_config(s, namespace_id)).limits.requests_per_minute

    retrieval = RetrievalService(
        sessions,
        providers,
        timeout_s=settings.retrieve_timeout_ms / 1000,
        reranker=reranker,
        rerank_timeout_s=settings.rerank_timeout_ms / 1000,
    )
    handlers: dict[JobType, Handler] = {
        **dict.fromkeys(SyncService.JOB_TYPES, sync.handle),
        JobType.MEMORY_EXTRACT: extraction.handle,
        JobType.MEMORY_RECONCILE: reconcile.handle,
        JobType.KB_SUGGEST: kb.handle,
        JobType.WEBHOOK_DELIVER: webhooks.handle,
        JobType.DOC_FETCH: documents.handle_fetch,
    }
    missing = set(JobType) - set(handlers)
    if missing:
        raise RuntimeError(f"no handler for job types {sorted(missing)}")

    async def dispatch(job: ClaimedJob) -> Any:
        return await handlers[job.type](job)

    jobs = JobQueue(sessions)
    worker = Worker(
        jobs,
        dispatch,
        ticks=(
            # late-bound: `episodes` is built below, with the worker's run_job
            Tick("flush idle episode buffers", IDLE_FLUSH_EVERY_S, lambda: episodes.flush_idle()),
            Tick("expire unanswered candidates", EXPIRE_EVERY_S, governance.expire_candidates),
            Tick("count retrieval hits", HITS_EVERY_S, retrieval.count_hits),
            Tick("forget old idempotency keys", PURGE_IDEMPOTENCY_EVERY_S, idempotency.purge),
            Tick(
                "schedule reconcile",
                settings.reconcile_interval_hours * 3600.0,
                lambda: schedule_reconcile(sessions),
            ),
        ),
    )
    episodes = EpisodeService(
        sessions,
        governance,
        providers.memory,
        worker.run_job,
        buffer_tokens=settings.buffer_tokens,
        buffer_idle=timedelta(minutes=settings.buffer_idle_minutes),
    )
    return Services(
        engine=engine,
        providers=providers,
        embedder=embedder,
        reranker=reranker,
        governance=governance,
        namespaces=NamespaceService(
            sessions,
            settings.token_secret.get_secret_value() if settings.token_secret else None,
        ),
        memories=MemoryService(sessions, governance, providers.memory),
        kb=kb,
        documents=documents,
        episodes=episodes,
        reconcile=reconcile,
        retrieval=retrieval,
        worker=worker,
        jobs=jobs,
        idempotency=idempotency,
        rate_limiter=RateLimiter(settings.rate_limit_per_minute, namespace_rpm),
        webhooks=webhooks,
        outbound=out,
        admin=AdminService(sessions),
        admin_token=settings.admin_token.get_secret_value() if settings.admin_token else None,
        embedding_model=settings.embedding_model if embedder is not None else None,
    )


@asynccontextmanager
async def runtime(
    settings: Settings,
    *,
    http: httpx2.AsyncClient | None = None,
    embedder: Embedder | None = None,
    memory_provider: MemoryProvider | None = None,
    llm: LLM | None = None,
    reranker: Reranker | None = None,
) -> AsyncIterator[Services]:
    """`http`, `embedder`, `memory_provider`, `llm` and `reranker` are injectable (tests stub the
    network, the models and the engine); whatever is created here is also closed here."""
    async with engine_scope(settings.database_url, schema=settings.db_schema) as engine:
        client = http or httpx2.AsyncClient()
        try:
            embed = embedder or (build_embedder(settings) if uses_native(settings) else None)
            providers = build_providers(settings, client, engine, embed, memory=memory_provider)
            try:
                roles = llm or build_llm(settings, providers)
                rerank = reranker or build_reranker(settings)
                yield build_services(settings, engine, providers, embed, roles, reranker=rerank)
            finally:
                await providers.aclose()
        finally:
            if http is None:
                await client.aclose()
