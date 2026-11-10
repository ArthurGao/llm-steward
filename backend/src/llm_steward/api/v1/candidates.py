import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from llm_steward.api.deps import OwnReader, OwnWriter, ServicesDep
from llm_steward.errors import Forbidden
from llm_steward.models.enums import CandidateStage, CandidateStatus
from llm_steward.schemas.kb import CandidateOut, CandidatePage, Decision, DecisionOut, EntryOut
from llm_steward.services.container import Services
from llm_steward.services.governance_service import Actor
from llm_steward.services.kb_service import CandidateFilter
from llm_steward.services.namespace_service import Principal
from llm_steward.services.paging import MAX_PAGE

router = APIRouter(prefix="/candidates", tags=["candidates"])


@router.get("")
async def list_candidates(
    principal: OwnReader,
    services: ServicesDep,
    stage: CandidateStage | None = None,
    status: CandidateStatus | None = CandidateStatus.PENDING,
    user_id: str | None = None,
    cursor: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 50,
) -> CandidatePage:
    rows, next_cursor = await services.kb.list_candidates(
        principal.namespace_id,
        CandidateFilter(stage, status, principal.pin_user(user_id)),
        cursor=cursor,
        limit=limit,
    )
    items = [CandidateOut.model_validate(c) for c in rows]
    return CandidatePage(items=items, next_cursor=next_cursor)


async def _decide(
    candidate_id: uuid.UUID,
    to: CandidateStatus,
    body: Decision,
    principal: Principal,
    services: ServicesDep,
) -> DecisionOut:
    draft = body.draft.model_dump(exclude_none=True) if body.draft else None
    if draft and principal.user_id is not None and body.scope == "team":
        # The card's text is what the user was asked about: under their own token they may take
        # it as is, or keep an edit to themselves, but not rewrite what the team will read.
        raise Forbidden("a user token can keep an edited card only as a personal memory")
    # an end user answering their card (a user token: only its own user), or the key itself
    # (a reviewer with the review scope)
    actor = principal.acting_for(body.user_id) if body.user_id else principal.actor
    return await decide(services, principal.namespace_id, candidate_id, to, body, actor)


async def decide(
    services: Services,
    namespace_id: uuid.UUID,
    candidate_id: uuid.UUID,
    to: CandidateStatus,
    body: Decision,
    actor: Actor,
) -> DecisionOut:
    """A decision as the API answers it (`/v1` and the Admin): the candidate, and the knowledge
    it became."""
    draft = body.draft.model_dump(exclude_none=True) if body.draft else None
    candidate, entry = await services.kb.decide(
        namespace_id, candidate_id, to, actor, draft=draft, scope=body.scope, note=body.note
    )
    out = DecisionOut.model_validate(candidate)
    out.entry = EntryOut.model_validate(entry) if entry else None
    return out


@router.post("/{candidate_id}/accept")
async def accept(
    candidate_id: uuid.UUID,
    principal: OwnWriter,
    services: ServicesDep,
    body: Decision | None = None,
) -> DecisionOut:
    return await _decide(
        candidate_id, CandidateStatus.ACCEPTED, body or Decision(), principal, services
    )


@router.post("/{candidate_id}/reject")
async def reject(
    candidate_id: uuid.UUID,
    principal: OwnWriter,
    services: ServicesDep,
    body: Decision | None = None,
) -> DecisionOut:
    return await _decide(
        candidate_id, CandidateStatus.REJECTED, body or Decision(), principal, services
    )
