"""Request dependencies: services and API-key authentication (default deny).

Every `/v1` route depends on `require(scope)`. The only routes without authentication are listed in
`UNAUTHENTICATED_PATHS`; a test walks the app's routes and fails on anything else left open.
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

_bearer = HTTPBearer(auto_error=False)
_CHALLENGE = {"WWW-Authenticate": "Bearer"}


def get_services(request: Request) -> Services:
    services: Services = request.app.state.services
    return services


ServicesDep = Annotated[Services, Depends(get_services)]


async def authenticate(
    services: ServicesDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal:
    if credentials is None:
        raise HTTPException(401, "missing API key", headers=_CHALLENGE)
    principal = await services.namespaces.authenticate(credentials.credentials)
    if principal is None:
        raise HTTPException(401, "invalid or revoked API key", headers=_CHALLENGE)
    return principal


def require(scope: ApiScope) -> Callable[..., Awaitable[Principal]]:
    async def dependency(principal: Annotated[Principal, Depends(authenticate)]) -> Principal:
        if scope not in principal.scopes:
            raise HTTPException(403, f"API key lacks the {scope.value!r} scope")
        return principal

    dependency.__name__ = f"require_{scope.value}"
    return dependency


Reader = Annotated[Principal, Depends(require(ApiScope.READ))]
Writer = Annotated[Principal, Depends(require(ApiScope.WRITE))]
Reviewer = Annotated[Principal, Depends(require(ApiScope.REVIEW))]
