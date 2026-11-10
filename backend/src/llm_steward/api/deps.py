"""Request dependencies: services and authentication (default deny).

Every `/v1` route depends on `require(scope)`. The only routes without authentication are listed in
`UNAUTHENTICATED_PATHS`; a test walks the app's routes and fails on anything else left open.

Two credentials: an API key (a host app's server) and a user token (one end user's browser, P5).
A user token is refused by every route except those whose `require` names a reason for letting
end users in (`end_user=`); a test pins that list. Those routes pin `user_id` to the token's user.
"""

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from llm_steward.models.enums import ApiScope
from llm_steward.services.container import Services
from llm_steward.services.namespace_service import Principal

# Deliberately open: liveness for Docker/monitoring, and the API description itself.
UNAUTHENTICATED_PATHS = frozenset(
    {"/health", "/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}
)

_bearer = HTTPBearer(
    auto_error=False, scheme_name="ApiKey", description="A namespace API key: stw_<prefix>_<secret>"
)
# Declared only by the routes open to end users, so the OpenAPI spec shows which those are.
_user_bearer = HTTPBearer(
    auto_error=False,
    scheme_name="UserToken",
    description="A user token from POST /v1/tokens/user: only that end user's own data",
)
_CHALLENGE = {"WWW-Authenticate": "Bearer"}


def get_services(request: Request) -> Services:
    services: Services = request.app.state.services
    return services


ServicesDep = Annotated[Services, Depends(get_services)]


async def authenticate(
    services: ServicesDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal:
    return await _principal(services, credentials)


async def authenticate_end_user(
    services: ServicesDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_user_bearer)],
) -> Principal:
    return await _principal(services, credentials)


async def _principal(
    services: Services, credentials: HTTPAuthorizationCredentials | None
) -> Principal:
    if credentials is None:
        raise HTTPException(401, "missing API key or user token", headers=_CHALLENGE)
    principal = await services.namespaces.authenticate(credentials.credentials)
    if principal is None:
        raise HTTPException(401, "invalid, expired or revoked credentials", headers=_CHALLENGE)
    return principal


def require(scope: ApiScope, *, end_user: str | None = None) -> Callable[..., Awaitable[Principal]]:
    """`end_user`: why an end user's own token may call this route; None (the default) refuses
    user tokens."""

    auth = authenticate_end_user if end_user is not None else authenticate

    async def dependency(principal: Annotated[Principal, Depends(auth)]) -> Principal:
        if principal.user_id is not None and end_user is None:
            raise HTTPException(403, "a user token cannot call this route")
        if scope not in principal.scopes:
            kind = "user token" if principal.user_id is not None else "API key"
            raise HTTPException(403, f"{kind} lacks the {scope.value!r} scope")
        return principal

    dependency.__name__ = f"require_{scope.value}" + ("" if end_user is None else "_or_end_user")
    return dependency


Reader = Annotated[Principal, Depends(require(ApiScope.READ))]
Writer = Annotated[Principal, Depends(require(ApiScope.WRITE))]
Reviewer = Annotated[Principal, Depends(require(ApiScope.REVIEW))]

# The end user's own data, through their browser (design §7: confirmation cards, "my memories").
_OWN_DATA = "an end user reads and changes their own memories, cards and retrievals"
OwnReader = Annotated[Principal, Depends(require(ApiScope.READ, end_user=_OWN_DATA))]
OwnWriter = Annotated[Principal, Depends(require(ApiScope.WRITE, end_user=_OWN_DATA))]
