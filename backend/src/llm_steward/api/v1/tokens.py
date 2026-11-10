from datetime import timedelta

from fastapi import APIRouter

from llm_steward.api.deps import ServicesDep, Writer
from llm_steward.schemas.auth import UserTokenIn, UserTokenOut

router = APIRouter(tags=["auth"])


@router.post("/tokens/user", status_code=201)
async def issue_user_token(
    body: UserTokenIn, principal: Writer, services: ServicesDep
) -> UserTokenOut:
    """A short-lived token for one end user, for calling llm-steward from their browser (design
    §7). It reaches only that user's own memories, cards and retrievals; revoking the API key that
    minted it ends it. Your server calls this with its API key; never ship the key to a browser.
    503 when the server has no STEWARD_TOKEN_SECRET."""
    issued = await services.namespaces.issue_user_token(
        principal, body.user_id, timedelta(seconds=body.ttl_seconds)
    )
    return UserTokenOut(token=issued.token, user_id=issued.user_id, expires_at=issued.expires_at)
