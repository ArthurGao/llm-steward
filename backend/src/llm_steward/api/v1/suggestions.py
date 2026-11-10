from fastapi import APIRouter, Response

from llm_steward.api.deps import OwnWriter, ServicesDep
from llm_steward.api.v1.kb import written
from llm_steward.schemas.kb import SuggestionIn, Written

router = APIRouter(tags=["governance"])


@router.post("/suggestions", status_code=201, responses={202: {"model": Written}})
async def suggest(
    body: SuggestionIn, principal: OwnWriter, services: ServicesDep, response: Response
) -> Written:
    """The host app proposes saving `text` to the KB (design §7): drafted by `kb.draft`, then
    routed by risk (201 live, 202 a candidate, a card when `user_id` is asked). Needs the KB LLM."""
    result = await services.kb.suggest(
        principal.namespace_id,
        body.collection_id,
        body.text,
        actor=principal.actor,
        user_id=principal.pin_user(body.user_id),
        session_id=body.session_id,
    )
    return written(result, response)
