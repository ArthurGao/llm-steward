import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Response

from llm_steward.api.deps import Reader, Reviewer, ServicesDep, Writer
from llm_steward.models.enums import KbStatus
from llm_steward.schemas.kb import (
    CandidateOut,
    CollectionCreate,
    CollectionOut,
    CollectionPage,
    CollectionPatch,
    EntryCreate,
    EntryOut,
    EntryPage,
    EntryPatch,
    Written,
)
from llm_steward.services.governance_service import (
    CreateCollection,
    TransitionKbEntry,
    UpdateCollection,
)
from llm_steward.services.kb_service import EntryFilter, Submitted
from llm_steward.services.paging import MAX_PAGE

router = APIRouter(prefix="/kb", tags=["kb"])


def written(result: Submitted, response: Response, live: int = 201) -> Written:
    """`live` (201 created, 200 edited) for live knowledge, 202 for a waiting candidate."""
    response.status_code = live if result.entry is not None else 202
    return Written(
        entry=EntryOut.model_validate(result.entry) if result.entry else None,
        candidate=CandidateOut.model_validate(result.candidate) if result.candidate else None,
    )


# --- collections (their review policy is a reviewer's decision) --------------------------------


@router.post("/collections", status_code=201)
async def create_collection(
    body: CollectionCreate, principal: Reviewer, services: ServicesDep
) -> CollectionOut:
    op = CreateCollection(principal.namespace_id, body.name, body.default_risk, body.review_policy)
    return CollectionOut.model_validate(await services.governance.apply(op, principal.actor))


@router.get("/collections")
async def list_collections(principal: Reader, services: ServicesDep) -> CollectionPage:
    rows = await services.kb.list_collections(principal.namespace_id)
    return CollectionPage(items=[CollectionOut.model_validate(c) for c in rows])


@router.patch("/collections/{collection_id}")
async def patch_collection(
    collection_id: uuid.UUID, body: CollectionPatch, principal: Reviewer, services: ServicesDep
) -> CollectionOut:
    op = UpdateCollection(
        principal.namespace_id, collection_id, body.default_risk, body.review_policy
    )
    return CollectionOut.model_validate(await services.governance.apply(op, principal.actor))


# --- entries -----------------------------------------------------------------------------------


@router.post("/entries", status_code=201, responses={202: {"model": Written}})
async def create_entry(
    body: EntryCreate, principal: Writer, services: ServicesDep, response: Response
) -> Written:
    """Submitted, then routed by risk (design §5): low goes live, medium asks `user_id` (or goes
    live without one), high waits for a reviewer. Personal data is redacted (namespace policy)."""
    result = await services.kb.submit(
        principal.namespace_id,
        body.collection_id,
        body.title,
        body.content,
        actor=principal.actor,
        risk_level=body.risk_level,
        user_id=body.user_id,
        tags=body.tags,
    )
    return written(result, response)


@router.get("/entries")
async def list_entries(
    principal: Reader,
    services: ServicesDep,
    collection_id: uuid.UUID | None = None,
    status: KbStatus | None = None,
    cursor: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 50,
) -> EntryPage:
    rows, next_cursor = await services.kb.list_entries(
        principal.namespace_id, EntryFilter(collection_id, status), cursor=cursor, limit=limit
    )
    return EntryPage(items=[EntryOut.model_validate(e) for e in rows], next_cursor=next_cursor)


@router.get("/entries/{entry_id}")
async def get_entry(entry_id: uuid.UUID, principal: Reader, services: ServicesDep) -> EntryOut:
    return EntryOut.model_validate(await services.kb.get_entry(principal.namespace_id, entry_id))


@router.patch("/entries/{entry_id}", responses={202: {"model": Written}})
async def patch_entry(
    entry_id: uuid.UUID,
    body: EntryPatch,
    principal: Writer,
    services: ServicesDep,
    response: Response,
) -> Written:
    """A new version. Verified knowledge, or a high-risk edit, waits for a reviewer (202)."""
    result = await services.kb.revise(
        principal.namespace_id,
        entry_id,
        actor=principal.actor,
        title=body.title,
        content=body.content,
    )
    return written(result, response, live=200)


@router.post("/entries/{entry_id}/verify")
async def verify_entry(entry_id: uuid.UUID, principal: Reviewer, services: ServicesDep) -> EntryOut:
    op = TransitionKbEntry(principal.namespace_id, entry_id, KbStatus.VERIFIED)
    return EntryOut.model_validate(await services.governance.apply(op, principal.actor))


@router.post("/entries/{entry_id}/deprecate")
async def deprecate_entry(
    entry_id: uuid.UUID, principal: Reviewer, services: ServicesDep
) -> EntryOut:
    """Out of retrieval at once (the safety net), out of the provider when the Syncer runs."""
    op = TransitionKbEntry(principal.namespace_id, entry_id, KbStatus.DEPRECATED)
    return EntryOut.model_validate(await services.governance.apply(op, principal.actor))
