from fastapi import APIRouter

from llm_steward.api.deps import Reader, ServicesDep
from llm_steward.providers.base import Scope
from llm_steward.schemas.kb import CandidateOut
from llm_steward.schemas.retrieve import RetrievedItemOut, RetrieveIn, RetrieveOut
from llm_steward.services.retrieval_service import RetrieveRequest

router = APIRouter(tags=["retrieval"])


@router.post("/retrieve")
async def retrieve(body: RetrieveIn, principal: Reader, services: ServicesDep) -> RetrieveOut:
    """Joint search over memory and KB: 200 with `degraded` if a source failed, 503 if all did."""
    result = await services.retrieval.retrieve(
        principal.namespace_id,
        RetrieveRequest(
            query=body.query,
            user_id=body.scope.user_id,
            agent_id=body.scope.agent_id,
            session_id=body.scope.session_id,
            sources=tuple(dict.fromkeys(body.sources)),
            collections=tuple(body.collections) if body.collections is not None else None,
            top_k=body.top_k,
            with_context=body.format == "context",
            as_of=body.as_of,
        ),
    )
    scope = Scope(principal.namespace_id, body.scope.user_id, None, body.scope.session_id)
    cards = await services.kb.cards(scope)
    return RetrieveOut(
        suggestions=[CandidateOut.model_validate(c) for c in cards],
        retrieval_id=result.retrieval_id,
        items=[RetrievedItemOut.model_validate(i) for i in result.items],
        degraded=result.degraded,
        context=result.context,
    )
