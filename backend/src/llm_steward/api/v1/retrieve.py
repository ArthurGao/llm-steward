import asyncio
import uuid

from fastapi import APIRouter

from llm_steward.api.deps import OwnReader, OwnWriter, ServicesDep
from llm_steward.providers.base import Scope
from llm_steward.schemas.kb import CandidateOut
from llm_steward.schemas.retrieve import FeedbackIn, RetrievedItemOut, RetrieveIn, RetrieveOut

router = APIRouter(tags=["retrieval"])


@router.post("/retrieve")
async def retrieve(body: RetrieveIn, principal: OwnReader, services: ServicesDep) -> RetrieveOut:
    """Joint search over memory and KB: 200 with `degraded` if a source failed, 503 if all did."""
    user_id = principal.pin_user(body.scope.user_id)
    scope = Scope(principal.namespace_id, user_id, None, body.scope.session_id)
    search = services.retrieval.retrieve(
        principal.namespace_id,
        body.to_request(user_id, with_context=body.format == "context"),
    )
    result, cards = await asyncio.gather(search, services.kb.cards(scope))  # independent reads
    return RetrieveOut(
        suggestions=[CandidateOut.model_validate(c) for c in cards],
        retrieval_id=result.retrieval_id,
        items=[RetrievedItemOut.model_validate(i) for i in result.items],
        degraded=result.degraded,
        context=result.context,
    )


@router.post("/retrieve/{retrieval_id}/feedback", status_code=204)
async def feedback(
    retrieval_id: uuid.UUID, body: FeedbackIn, principal: OwnWriter, services: ServicesDep
) -> None:
    """Was a returned item helpful? Recorded per answer; KB entries count it."""
    await services.retrieval.feedback(
        principal.namespace_id,
        retrieval_id,
        body.item_id,
        helpful=body.helpful,
        note=body.note,
        by=principal.actor.id,
        user_id=principal.user_id,
    )
