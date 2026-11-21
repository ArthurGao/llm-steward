"""Wires one runtime: engine, HTTP client, embedder, providers, LLM roles and services.

`runtime()` is the single place both entry points (the API lifespan and `steward worker`) build
from, so they can never drift apart in how providers are configured. A configuration that cannot
serve a needed LLM role fails here, at startup (KB llm-app-architecture §1.5).
"""

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import httpx2
from sqlalchemy.ext.asyncio import AsyncEngine

from llm_steward.config import Settings
from llm_steward.llm.client import LLM, LiteModelLLM, open_cache_store, role_llm, tool_model
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
from llm_steward.services.extraction_service import (
    ExtractionService,
    compensates,
    extraction_mode,
)
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
from llm_steward.services.triage_service import TriageService
from llm_steward.services.webhook_service import WebhookService
from llm_steward.services.worker import Tick, Worker
from llm_steward.storage.db import engine_scope, libpq_url, rls_bypassed, session_factory

if TYPE_CHECKING:  # the `cache` extra
    from lite_cache.store import PgStore

Handler = Callable[[ClaimedJob], Awaitable[Any]]

log = logging.getLogger(__name__)
IDLE_FLUSH_EVERY_S = 60.0
REVIEW_EVERY_S = 24 * 3600.0  # feedback-driven review flags (design §5 maintenance)
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


def build_llm(
    settings: Settings, providers: Providers, cache_store: "PgStore | None" = None
) -> LiteModelLLM | None:
    """Exactly the roles that will be used, resolved now (a missing model fails at startup):
    `memory.extract` when llm-steward extracts for the engine (`memory_llm`), `memory.resolve`
    when conflict compensation runs, the KB roles when `kb_llm` is on. `cache_store`: the
    response cache's store (`caches`)."""
    memory = providers.memory
    mode = extraction_mode(memory, settings.memory_llm)
    roles = [Role.MEMORY_EXTRACT] if mode == "steward" else []
    roles += [Role.MEMORY_RESOLVE] if compensates(memory, mode) else []
    if settings.memory_llm and mode == "engine":
        log.info("STEWARD_MEMORY_LLM is unused: the %s engine extracts itself", memory.name)
    if settings.kb_llm:
        roles += [Role.KB_RISK, Role.KB_WORTHINESS, Role.KB_DRAFT]
    else:
        log.info("KB LLM steps are off (STEWARD_KB_LLM): rules-only risk, no suggestions")
    return role_llm(settings, tuple(roles), cache_store) if roles else None


def caches(settings: Settings) -> bool:
    """Whether the response cache has a role to serve: it caches only KB labels (P10)."""
    if settings.cache.enabled and not settings.kb_llm:
        log.info("STEWARD_CACHE__ENABLED is unused: it caches KB labels and STEWARD_KB_LLM is off")
    return settings.cache.enabled and settings.kb_llm


async def _triage_off(job: ClaimedJob) -> int:
    log.info("reconcile triage is off (STEWARD_AGENT__RECONCILE_TRIAGE): job %s dropped", job.id)
    return 0


async def open_triage(
    settings: Settings, engine: AsyncEngine, providers: Providers, stack: AsyncExitStack
) -> TriageService | None:
    """The reconcile triage assistant when it is on (P11): its model, and lite-agent's session
    store in its own schema (migrated when behind, off the event loop), closed by `stack`."""
    if not settings.agent.reconcile_triage:
        return None
    from lite_agent.pg import PgSessionStore

    model, tag = tool_model(settings, Role.AGENT_TRIAGE)
    stack.push_async_callback(model.aclose)
    url = libpq_url(settings.database_url)
    assert url is not None  # Settings refuse the assistant on SQLite
    await asyncio.to_thread(_migrate_agent_schema, url, settings.agent.db_schema)
    store = PgSessionStore(url, schema=settings.agent.db_schema)
    stack.push_async_callback(store.close)
    return TriageService(
        session_factory(engine),
        providers.memory,
        model,
        store,
        model_tag=tag,
        max_orphans=settings.agent.max_orphans,
        turns=settings.agent.turns,
    )


def _migrate_agent_schema(url: str, schema: str) -> None:
    import psycopg
    from lite_agent.pg import migrate

    with psycopg.connect(url) as conn:
        migrate(conn, schema)


def build_services(
    settings: Settings,
    engine: AsyncEngine,
    providers: Providers,
    embedder: Embedder | None,
    llm: LLM | None,
    *,
    reranker: Reranker | None,
    triage: TriageService | None = None,
) -> Services:
    sessions = session_factory(engine)
    governance = GovernanceService(
        sessions, memory_provider=providers.memory.name, kb_provider=providers.kb.name
    )
    sync = SyncService(sessions, providers)
    kb = KbService(sessions, governance, llm if settings.kb_llm else None)
    extraction = ExtractionService(
        sessions,
        governance,
        providers.memory,
        llm,
        mode=extraction_mode(providers.memory, settings.memory_llm),
        on_extracted=kb.queue_suggestions,
    )
    extracts = extraction.mode != "none"
    reconcile = ReconcileService(
        sessions, providers.memory, triage=triage is not None, governance=governance
    )
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
        # a job queued while the assistant was on, after it was turned off: dropped
        JobType.RECONCILE_TRIAGE: triage.handle if triage else _triage_off,
        JobType.RECONCILE_REMIRROR: reconcile.remirror,
    }
    missing = set(JobType) - set(handlers)
    if missing:
        raise RuntimeError(f"no handler for job types {sorted(missing)}")

    async def dispatch(job: ClaimedJob) -> Any:
        return await handlers[job.type](job)

    jobs = JobQueue(sessions)
    # Idle mode needs requests to wake it: only for the worker inside the API process.
    idle = settings.worker_idle_after_s if settings.run_worker else None
    worker = Worker(
        jobs,
        dispatch,
        idle_after_s=idle,
        # Idle: wake one flush interval after a buffer could have gone idle, so it is flushed.
        idle_ticks_after_s=settings.buffer_idle_minutes * 60 + IDLE_FLUSH_EVERY_S,
        ticks=(
            # late-bound: `episodes` is built below, with the worker's run_job
            Tick("flush idle episode buffers", IDLE_FLUSH_EVERY_S, lambda: episodes.flush_idle()),
            Tick("expire unanswered candidates", EXPIRE_EVERY_S, governance.expire_candidates),
            Tick("count retrieval hits", HITS_EVERY_S, retrieval.count_hits),
            Tick("flag knowledge for review", REVIEW_EVERY_S, kb.flag_for_review),
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
        extracts=extracts,
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
        memories=MemoryService(sessions, governance, providers.memory, extracts=extracts),
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
    async with (
        engine_scope(settings.database_url, schema=settings.db_schema) as engine,
        AsyncExitStack() as stack,
    ):
        if settings.require_rls and (why := await rls_bypassed(engine)) is not None:
            raise RuntimeError(f"STEWARD_REQUIRE_RLS: {why}")
        client = http or httpx2.AsyncClient()
        if http is None:
            stack.push_async_callback(client.aclose)
        embed = embedder or (build_embedder(settings) if uses_native(settings) else None)
        providers = build_providers(settings, client, engine, embed, memory=memory_provider)
        stack.push_async_callback(providers.aclose)
        if llm is None:
            store = None
            if caches(settings):  # a blocking connect and migration check: off the event loop
                store = await asyncio.to_thread(open_cache_store, settings)
                stack.push_async_callback(asyncio.to_thread, store.close)
            llm = built = build_llm(settings, providers, store)
            if built is not None:
                stack.push_async_callback(built.aclose)  # its SDK clients, after the services
        rerank = reranker or build_reranker(settings)
        triage = await open_triage(settings, engine, providers, stack)
        yield build_services(
            settings, engine, providers, embed, llm, reranker=rerank, triage=triage
        )
