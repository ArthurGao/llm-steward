"""`/admin/v1` (design §9): the Admin's API. No login and no accounts (decided 2026-11-10): one
static bearer token, `STEWARD_ADMIN_TOKEN`; the API is off (503) without it. Every route here
depends on it (router-level, default deny) and acts as the admin: a reviewer, recorded as
`admin` in the change log. Namespaced routes take the namespace's slug.

Writes go through the same services as `/v1` (governance, the outbox); what only the Admin reads
comes from `AdminService`. Not here, by design: anything that writes to an engine directly.
"""

import hmac
import uuid
from dataclasses import asdict, replace
from datetime import datetime
from typing import Annotated

import yaml
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from llm_steward.api.deps import ServicesDep
from llm_steward.api.v1.candidates import decide
from llm_steward.api.v1.kb import written
from llm_steward.errors import Unprocessable
from llm_steward.models import Namespace, RetrievalLog
from llm_steward.models.enums import (
    ActorType,
    ApiScope,
    AuthorType,
    CandidateStage,
    CandidateStatus,
    CandidateTarget,
    JobStatus,
    JobType,
    KbOrigin,
    KbStatus,
    MemoryStatus,
    ObjectType,
    RiskLevel,
    SyncStatus,
)
from llm_steward.prompts import templates as prompt_templates
from llm_steward.providers.base import Scope
from llm_steward.schemas import admin as out
from llm_steward.schemas.jobs import JobOut
from llm_steward.schemas.kb import (
    CandidateOut,
    CandidatePage,
    CollectionOut,
    Decision,
    DecisionOut,
    DocumentOut,
    DocumentPage,
    EntryOut,
    EntryPage,
    EntryPatch,
    Written,
)
from llm_steward.schemas.memories import (
    ChangeOut,
    ErasedOut,
    MemoryBrief,
    MemoryHistoryOut,
    MemoryOut,
    MemoryPage,
    MemoryPatch,
)
from llm_steward.schemas.retrieve import RetrievedItemOut, RetrieveIn, RetrieveOut
from llm_steward.services.admin_service import ActivityFilter, is_gap
from llm_steward.services.document_service import DocumentFilter
from llm_steward.services.governance_service import (
    Actor,
    PatchMemory,
    SubmitKbEntry,
    TransitionKbEntry,
)
from llm_steward.services.health_service import check_health
from llm_steward.services.kb_service import CandidateFilter, EntryFilter, ReviewFilter, Submitted
from llm_steward.services.memory_service import MemoryFilter
from llm_steward.services.paging import MAX_PAGE

ADMIN = Actor(ActorType.ADMIN, "admin")

_bearer = HTTPBearer(
    auto_error=False,
    scheme_name="AdminToken",
    description="The Admin's static token (STEWARD_ADMIN_TOKEN); no login, no accounts",
)


async def admin_auth(
    services: ServicesDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> None:
    secret = services.admin_token
    if secret is None:
        raise HTTPException(503, "the Admin API is off: set STEWARD_ADMIN_TOKEN")
    if credentials is None or not hmac.compare_digest(
        credentials.credentials.encode(), secret.encode()
    ):
        raise HTTPException(
            401, "missing or wrong admin token", headers={"WWW-Authenticate": "Bearer"}
        )


router = APIRouter(prefix="/admin/v1", tags=["admin"], dependencies=[Depends(admin_auth)])


async def _namespace(slug: str, services: ServicesDep) -> Namespace:
    return await services.namespaces.get_namespace(slug)


Ns = Annotated[Namespace, Depends(_namespace)]
Limit = Annotated[int, Query(ge=1, le=MAX_PAGE)]


# --- namespaces and the dashboard ---------------------------------------------------------------


@router.get("/namespaces")
async def namespaces(services: ServicesDep) -> out.NamespacePage:
    providers = services.providers
    return out.NamespacePage(
        items=[
            out.NamespaceOut(
                id=n.id,
                slug=n.slug,
                name=n.name,
                memory_provider=providers.memory.name,
                kb_provider=providers.kb.name,
            )
            for n in await services.admin.namespaces()
        ]
    )


@router.get("/ns/{slug}/stats")
async def stats(ns: Ns, services: ServicesDep) -> out.StatsOut:
    return out.StatsOut.model_validate(asdict(await services.admin.stats(ns.id)))


# --- memories and users -------------------------------------------------------------------------


@router.get("/ns/{slug}/memories")
async def list_memories(
    ns: Ns,
    services: ServicesDep,
    user_id: str | None = None,
    memory_type: str | None = None,
    status: MemoryStatus | None = None,
    tag: str | None = None,
    cursor: uuid.UUID | None = None,
    limit: Limit = 50,
) -> MemoryPage:
    filters = MemoryFilter(user_id, None, None, memory_type, status, tag)
    views, next_cursor = await services.memories.list_memories(
        ns.id, filters, cursor=cursor, limit=limit
    )
    return MemoryPage(items=[MemoryOut.of(v) for v in views], next_cursor=next_cursor)


@router.get("/ns/{slug}/memories/{memory_id}")
async def get_memory(ns: Ns, memory_id: uuid.UUID, services: ServicesDep) -> MemoryOut:
    return MemoryOut.of(await services.memories.get(ns.id, memory_id))


@router.patch("/ns/{slug}/memories/{memory_id}")
async def patch_memory(
    ns: Ns, memory_id: uuid.UUID, body: MemoryPatch, services: ServicesDep
) -> MemoryOut:
    op = PatchMemory(
        ns.id,
        memory_id,
        pinned=body.pinned,
        importance=body.importance,
        confidence=body.confidence,
        tags=body.tags,
        meta=body.metadata,
    )
    return MemoryOut.of(await services.memories.patch(op, actor=ADMIN))


@router.delete("/ns/{slug}/memories/{memory_id}")
async def delete_memory(ns: Ns, memory_id: uuid.UUID, services: ServicesDep) -> MemoryOut:
    return MemoryOut.of(await services.memories.delete(ns.id, memory_id, actor=ADMIN))


@router.get("/ns/{slug}/memories/{memory_id}/history")
async def memory_history(ns: Ns, memory_id: uuid.UUID, services: ServicesDep) -> MemoryHistoryOut:
    h = await services.memories.history(ns.id, memory_id)
    return MemoryHistoryOut(
        memory_id=memory_id,
        changes=[ChangeOut.model_validate(c) for c in h.changes],
        chain=[MemoryBrief.model_validate(m) for m in h.chain],
    )


@router.post("/ns/{slug}/memories/{memory_id}/promote", responses={202: {"model": Written}})
async def promote(
    ns: Ns,
    memory_id: uuid.UUID,
    services: ServicesDep,
    response: Response,
    collection_id: uuid.UUID | None = None,
) -> Written:
    """Rewritten as general knowledge (needs the KB LLM) into `collection_id`, else the
    namespace's suggestion collection; proposed for review, like any agent's suggestion."""
    target = collection_id or await services.kb.suggestion_collection(ns.id)
    result = await services.kb.promote(ns.id, memory_id, target, actor=_proposer())
    return written(result, response)


@router.delete("/ns/{slug}/memories")
async def erase_user(
    ns: Ns, services: ServicesDep, user_id: Annotated[str, Query(min_length=1)]
) -> ErasedOut:
    erased = await services.memories.erase_user(ns.id, user_id, actor=ADMIN)
    return ErasedOut(user_id=user_id, **asdict(erased))


@router.get("/ns/{slug}/users")
async def users(
    ns: Ns, services: ServicesDep, cursor: str | None = None, limit: Limit = 100
) -> out.UserPage:
    """Most recently active first. `cursor` is the previous page's `next_cursor` (opaque)."""
    found, last = await services.admin.users(ns.id, after=_user_cursor(cursor), limit=limit)
    return out.UserPage(
        items=[out.UserSummaryOut.model_validate(asdict(u)) for u in found],
        next_cursor=f"{last[0].isoformat()}|{last[1]}" if last else None,
    )


def _user_cursor(cursor: str | None) -> tuple[datetime, str] | None:
    if cursor is None:
        return None
    at, _, user = cursor.partition("|")  # an ISO time has no "|"; a user id may
    try:
        return datetime.fromisoformat(at), user
    except ValueError:
        raise HTTPException(422, "cursor is not one this endpoint returned") from None


@router.get("/ns/{slug}/users/{user_id}")
async def user(ns: Ns, user_id: str, services: ServicesDep) -> out.UserDetailOut:
    summary = await services.admin.user(ns.id, user_id)
    if summary is None:
        raise HTTPException(404, f"user {user_id!r} has no memories here")
    profile = await services.memories.profile(Scope(ns.id, user_id))
    return out.UserDetailOut(
        **asdict(summary),
        profile=profile.values,
        pinned=[MemoryBrief.model_validate(m) for m in profile.pinned],
    )


# --- knowledge ----------------------------------------------------------------------------------


@router.get("/ns/{slug}/kb/collections")
async def collections(ns: Ns, services: ServicesDep) -> out.CollectionCountsPage:
    rows = await services.admin.collections(ns.id)
    return out.CollectionCountsPage(
        items=[
            out.CollectionCountsOut(
                **CollectionOut.model_validate(r.collection).model_dump(),
                entry_count=r.entry_count,
                document_count=r.document_count,
            )
            for r in rows
        ]
    )


@router.get("/ns/{slug}/kb/entries")
async def list_entries(
    ns: Ns,
    services: ServicesDep,
    collection_id: uuid.UUID | None = None,
    status: KbStatus | None = None,
    review: ReviewFilter | None = None,
    cursor: uuid.UUID | None = None,
    limit: Limit = 50,
) -> EntryPage:
    rows, next_cursor = await services.kb.list_entries(
        ns.id, EntryFilter(collection_id, status, review), cursor=cursor, limit=limit
    )
    return EntryPage(items=[EntryOut.model_validate(e) for e in rows], next_cursor=next_cursor)


@router.get("/ns/{slug}/kb/entries/{entry_id}")
async def get_entry(ns: Ns, entry_id: uuid.UUID, services: ServicesDep) -> EntryOut:
    return EntryOut.model_validate(await services.kb.get_entry(ns.id, entry_id))


@router.get("/ns/{slug}/kb/entries/{entry_id}/history")
async def entry_history(ns: Ns, entry_id: uuid.UUID, services: ServicesDep) -> out.EntryHistoryOut:
    """Every version, newest first: an edit's `before` is the text the previous version had."""
    entry, changes, obsoletes, by = await services.admin.entry_history(ns.id, entry_id)
    versions: list[out.EntryVersionOut] = []
    for change in changes:
        after = change.after or {}
        versions.append(
            out.EntryVersionOut(
                version=int(after.get("version", entry.version)),
                title=str(after.get("title", entry.title)),
                content=str(after.get("content", entry.content)),
                status=str(after.get("status", entry.status.value)),
                changed_at=change.created_at,
                actor_type=change.actor_type,
                actor_id=change.actor_id,
                reason=change.reason,
            )
        )
    return out.EntryHistoryOut(
        entry_id=entry.id,
        versions=list(reversed(versions)),
        obsoletes=[out.EntryLink.model_validate(e) for e in obsoletes],
        obsoleted_by=out.EntryLink.model_validate(by) if by else None,
    )


@router.patch("/ns/{slug}/kb/entries/{entry_id}", responses={202: {"model": Written}})
async def patch_entry(
    ns: Ns, entry_id: uuid.UUID, body: EntryPatch, services: ServicesDep, response: Response
) -> Written:
    """A new version (the admin reviews, so it applies at once; the text is screened)."""
    result = await services.kb.revise(
        ns.id, entry_id, actor=ADMIN, title=body.title, content=body.content
    )
    return written(result, response, live=200)


@router.post("/ns/{slug}/kb/entries/{entry_id}/verify")
async def verify_entry(ns: Ns, entry_id: uuid.UUID, services: ServicesDep) -> EntryOut:
    op = TransitionKbEntry(ns.id, entry_id, KbStatus.VERIFIED)
    return EntryOut.model_validate(await services.governance.apply(op, ADMIN))


@router.post("/ns/{slug}/kb/entries/{entry_id}/deprecate")
async def deprecate_entry(
    ns: Ns, entry_id: uuid.UUID, services: ServicesDep, reason: str | None = None
) -> EntryOut:
    op = TransitionKbEntry(ns.id, entry_id, KbStatus.DEPRECATED, reason=reason)
    return EntryOut.model_validate(await services.governance.apply(op, ADMIN))


@router.get("/ns/{slug}/kb/documents")
async def list_documents(
    ns: Ns,
    services: ServicesDep,
    collection_id: uuid.UUID | None = None,
    state: KbStatus | None = None,
    cursor: uuid.UUID | None = None,
    limit: Limit = 50,
) -> DocumentPage:
    rows, next_cursor = await services.documents.list(
        ns.id, DocumentFilter(collection_id, state), cursor=cursor, limit=limit
    )
    return DocumentPage(
        items=[DocumentOut.model_validate(d) for d in rows], next_cursor=next_cursor
    )


# --- the review queue ---------------------------------------------------------------------------


@router.get("/ns/{slug}/candidates")
async def list_candidates(
    ns: Ns,
    services: ServicesDep,
    stage: CandidateStage | None = None,
    status: CandidateStatus | None = CandidateStatus.PENDING,
    user_id: str | None = None,
    cursor: uuid.UUID | None = None,
    limit: Limit = 50,
) -> CandidatePage:
    rows, next_cursor = await services.kb.list_candidates(
        ns.id, CandidateFilter(stage, status, user_id), cursor=cursor, limit=limit
    )
    return CandidatePage(
        items=[CandidateOut.model_validate(c) for c in rows], next_cursor=next_cursor
    )


@router.get("/ns/{slug}/candidates/{candidate_id}")
async def get_candidate(
    ns: Ns, candidate_id: uuid.UUID, services: ServicesDep
) -> out.CandidateDetailOut:
    candidate = await services.kb.get_candidate(ns.id, candidate_id)
    detail = out.CandidateDetailOut.model_validate(candidate)
    if candidate.target_type == CandidateTarget.KB_ENTRY and candidate.target_id is not None:
        target = await services.kb.get_entry(ns.id, candidate.target_id)
        detail.target = EntryOut.model_validate(target)
    return detail


@router.post("/ns/{slug}/candidates/{candidate_id}/accept")
async def accept(
    ns: Ns, candidate_id: uuid.UUID, services: ServicesDep, body: Decision | None = None
) -> DecisionOut:
    return await decide(
        services, ns.id, candidate_id, CandidateStatus.ACCEPTED, body or Decision(), ADMIN
    )


@router.post("/ns/{slug}/candidates/{candidate_id}/reject")
async def reject(
    ns: Ns, candidate_id: uuid.UUID, services: ServicesDep, body: Decision | None = None
) -> DecisionOut:
    """A rejection from the review queue says why (design §9; governance requires `note`)."""
    decision = body or Decision()
    return await decide(services, ns.id, candidate_id, CandidateStatus.REJECTED, decision, ADMIN)


# --- retrieval ----------------------------------------------------------------------------------


@router.post("/ns/{slug}/retrieve")
async def retrieve(ns: Ns, body: RetrieveIn, services: ServicesDep) -> RetrieveOut:
    """The Playground: the same retrieval as `/v1/retrieve`, logged like it."""
    result = await services.retrieval.retrieve(
        ns.id, body.to_request(body.scope.user_id, with_context=True)
    )
    return RetrieveOut(
        suggestions=[],
        retrieval_id=result.retrieval_id,
        items=[RetrievedItemOut.model_validate(i) for i in result.items],
        degraded=result.degraded,
        context=result.context,
    )


@router.get("/ns/{slug}/retrieval-logs")
async def retrieval_logs(
    ns: Ns,
    services: ServicesDep,
    gaps: bool = False,
    cursor: uuid.UUID | None = None,
    limit: Limit = 50,
) -> out.RetrievalLogPage:
    rows, next_cursor = await services.admin.retrieval_logs(
        ns.id, gaps_only=gaps, cursor=cursor, limit=limit
    )
    return out.RetrievalLogPage(items=[_log_out(r) for r in rows], next_cursor=next_cursor)


def _log_out(log: RetrievalLog) -> out.RetrievalLogOut:
    scores = [float(r.get("score", 0.0)) for r in log.results]
    latency = (log.latency_ms or {}).get("total")
    return out.RetrievalLogOut(
        id=log.id,
        query=log.query,
        user_id=(log.filters or {}).get("user_id"),
        top_score=max(scores) if scores else None,
        hit_count=log.hit_count,
        latency_ms=int(latency) if latency is not None else None,
        degraded=list((log.latency_ms or {}).get("degraded", [])),
        gap=is_gap(log),
        created_at=log.created_at,
    )


@router.post(
    "/ns/{slug}/retrieval-logs/{log_id}/add-to-kb",
    status_code=202,
    responses={201: {"model": Written}},
)
async def add_to_kb(
    ns: Ns,
    log_id: uuid.UUID,
    body: out.AddToKbIn,
    services: ServicesDep,
    response: Response,
) -> Written:
    """The missing answer to a retrieval, proposed as knowledge: a candidate for review (the
    admin writes it, a reviewer approves it; the log is its source)."""
    log = await services.admin.retrieval_log(ns.id, log_id)
    op = SubmitKbEntry(
        namespace_id=ns.id,
        collection_id=body.collection_id or await services.kb.suggestion_collection(ns.id),
        title=body.title,
        content=body.content,
        risk_level=RiskLevel.LOW,
        origin=KbOrigin.MANUAL,
        author_type=AuthorType.HUMAN,
        source_refs=[{"type": "retrieval", "id": str(log.id)}],
        reason=f"missing answer to: {log.query}"[:500],
    )
    result = await services.governance.apply(op, _proposer())
    return written(Submitted.of(result), response)


def _proposer() -> Actor:
    return replace(ADMIN, propose_only=True)


# --- activity, jobs, providers ------------------------------------------------------------------


@router.get("/ns/{slug}/activity")
async def activity(
    ns: Ns,
    services: ServicesDep,
    object_type: ObjectType | None = None,
    object_id: uuid.UUID | None = None,
    actor_type: ActorType | None = None,
    cursor: uuid.UUID | None = None,
    limit: Limit = 50,
) -> out.ActivityPage:
    rows, next_cursor = await services.admin.activity(
        ns.id, ActivityFilter(object_type, object_id, actor_type), cursor=cursor, limit=limit
    )
    return out.ActivityPage(
        items=[out.ActivityOut.model_validate(c) for c in rows], next_cursor=next_cursor
    )


@router.get("/ns/{slug}/jobs")
async def jobs(
    ns: Ns,
    services: ServicesDep,
    status: JobStatus | None = None,
    type: JobType | None = None,
    cursor: uuid.UUID | None = None,
    limit: Limit = 50,
) -> out.AdminJobPage:
    rows, next_cursor = await services.admin.jobs(
        ns.id, status=status, job_type=type, cursor=cursor, limit=limit
    )
    return out.AdminJobPage(items=[JobOut.model_validate(j) for j in rows], next_cursor=next_cursor)


@router.post("/ns/{slug}/jobs/{job_id}/retry")
async def retry_job(ns: Ns, job_id: uuid.UUID, services: ServicesDep) -> JobOut:
    return JobOut.model_validate(await services.admin.retry_job(ns.id, job_id))


@router.post("/ns/{slug}/jobs/{job_id}/cancel")
async def cancel_job(ns: Ns, job_id: uuid.UUID, services: ServicesDep) -> JobOut:
    return JobOut.model_validate(await services.admin.cancel_job(ns.id, job_id))


@router.get("/ns/{slug}/providers")
async def providers(ns: Ns, services: ServicesDep) -> out.ProviderPage:
    probe = check_health(services.engine, services.providers.by_role())
    rows = await services.admin.providers(ns.id, services.providers, probe)
    return out.ProviderPage(items=[out.ProviderOut.model_validate(asdict(r)) for r in rows])


@router.get("/ns/{slug}/sync")
async def sync_refs(
    ns: Ns,
    services: ServicesDep,
    status: SyncStatus | None = None,
    cursor: uuid.UUID | None = None,
    limit: Limit = 50,
) -> out.SyncRefPage:
    rows, next_cursor = await services.admin.sync_refs(
        ns.id, status=status, cursor=cursor, limit=limit
    )
    return out.SyncRefPage(
        items=[out.SyncRefOut.model_validate(r) for r in rows], next_cursor=next_cursor
    )


@router.post("/ns/{slug}/sync/{ref_id}/retry")
async def retry_sync(ns: Ns, ref_id: uuid.UUID, services: ServicesDep) -> out.SyncRefOut:
    return out.SyncRefOut.model_validate(await services.admin.retry_sync(ns.id, ref_id))


# --- settings and keys --------------------------------------------------------------------------


@router.get("/ns/{slug}/settings")
async def settings(ns: Ns, services: ServicesDep) -> out.SettingsOut:
    templates = [out.TemplateOut(name=name, body=body) for name, body in prompt_templates()]
    document = yaml.safe_dump(ns.config or {}, allow_unicode=True, sort_keys=False)
    return out.SettingsOut(
        yaml=document, templates=templates, embedding_model=services.embedding_model
    )


@router.put("/ns/{slug}/settings")
async def save_settings(ns: Ns, body: out.SettingsIn, services: ServicesDep) -> out.SettingsOut:
    """The whole config, as YAML, validated before anything is written (`steward ns apply`)."""
    try:
        raw = yaml.safe_load(body.yaml) or {}
    except yaml.YAMLError as exc:
        raise Unprocessable(f"not YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise Unprocessable("the config must be a mapping")
    saved, _ = await services.namespaces.apply_config(ns.slug, raw)
    return await settings(saved, services)


@router.get("/ns/{slug}/keys")
async def keys(ns: Ns, services: ServicesDep) -> out.ApiKeyPage:
    return out.ApiKeyPage(
        items=[out.ApiKeyOut.model_validate(k) for k in await services.admin.keys(ns.id)]
    )


@router.post("/ns/{slug}/keys", status_code=201)
async def create_key(ns: Ns, body: out.ApiKeyIn, services: ServicesDep) -> out.ApiKeyCreatedOut:
    """The key is in this answer once and never again (only its hash is kept)."""
    scopes = tuple(ApiScope(s) for s in dict.fromkeys(body.scopes))
    row, key = await services.namespaces.issue_key(ns.slug, scopes, body.name)
    return out.ApiKeyCreatedOut(**out.ApiKeyOut.model_validate(row).model_dump(), key=key)


@router.delete("/ns/{slug}/keys/{key_id}")
async def revoke_key(ns: Ns, key_id: uuid.UUID, services: ServicesDep) -> out.ApiKeyOut:
    return out.ApiKeyOut.model_validate(await services.admin.revoke_key(ns.id, key_id))
