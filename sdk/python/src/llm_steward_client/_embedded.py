"""Embedded mode (design §7): llm-steward inside the caller's process, no server (extra
`embedded`). The same app and the same code paths as the server, reached through an in-process
ASGI transport; the outbox worker runs alongside.

The database is migrated on start and a namespace (`default`) is created if missing. There is
no API key: a process that owns the database is the only caller, so every request runs as a
local principal of that namespace with `read` and `write` (and `review` only if asked for; a
reviewer's writes go live verified). Native engines (SQLite + sqlite-vec, local embeddings)
unless `settings` say otherwise; rate limiting is off.

    st = Steward.embedded("sqlite+aiosqlite:///./steward.db")
    ...
    st.close()
"""

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, ExitStack, asynccontextmanager
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from llm_steward_client._client import AsyncSteward, Steward

# The local principal's key id: fixed, so its writes read the same in the change log every run.
LOCAL_KEY_ID = uuid.uuid5(uuid.NAMESPACE_URL, "llm-steward:embedded")
_BASE_URL = "http://embedded"


def _settings(database_url: str, overrides: dict[str, Any] | None) -> Any:
    try:
        from llm_steward.config import Settings
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "embedded mode needs: pip install 'llm-steward-client[embedded]'"
        ) from exc
    values: dict[str, Any] = {
        "database_url": database_url,
        "memory_provider": "native",
        "kb_provider": "native",
        "rate_limit_per_minute": None,
        **(overrides or {}),
    }
    return Settings(_env_file=None, **values)


def _migrate(settings: Any) -> None:
    from alembic import command
    from llm_steward.cli import alembic_config

    command.upgrade(alembic_config(settings.database_url, settings.db_schema), "head")


async def _local_principal(app: Any, namespace: str, review: bool) -> Any:
    from llm_steward.errors import NotFound
    from llm_steward.models.enums import ApiScope
    from llm_steward.services.namespace_service import Principal

    services = app.state.services
    try:
        found = await services.namespaces.get_namespace(namespace)
    except NotFound:
        found, _ = await services.namespaces.apply_config(namespace, {})
    scopes = {ApiScope.READ, ApiScope.WRITE} | ({ApiScope.REVIEW} if review else set())
    return Principal(found.id, LOCAL_KEY_ID, frozenset(scopes))


class _AsLocal:
    """Every request already resolved to the local principal (the auth step reads it from here)."""

    def __init__(self, app: Any, principal: Any) -> None:
        from llm_steward.services.namespace_service import PRINCIPAL_STATE_KEY

        self.app, self.principal, self.key = app, principal, PRINCIPAL_STATE_KEY

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            scope = {**scope, "state": {**scope.get("state", {}), self.key: self.principal}}
        await self.app(scope, receive, send)


@asynccontextmanager
async def _running(
    settings: Any, app_overrides: dict[str, Any] | None, namespace: str, review: bool
) -> AsyncIterator[Any]:
    """The app started (its lifespan: services, the worker) and wrapped as the local caller."""
    from llm_steward.main import create_app

    app = create_app(settings, **(app_overrides or {}))
    async with app.router.lifespan_context(app):
        yield _AsLocal(app, await _local_principal(app, namespace, review))


async def embedded_async(
    cls: "type[AsyncSteward]",
    database_url: str,
    *,
    namespace: str = "default",
    review: bool = False,
    settings: dict[str, Any] | None = None,
    app_overrides: dict[str, Any] | None = None,
) -> "AsyncSteward":
    """`settings`: Settings fields (e.g. engines, models); `app_overrides`: create_app's
    injectables (tests pass a fake embedder)."""
    config = _settings(database_url, settings)
    await asyncio.to_thread(_migrate, config)
    async with AsyncExitStack() as stack:
        app = await stack.enter_async_context(_running(config, app_overrides, namespace, review))
        http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=_BASE_URL)
        stack.push_async_callback(http.aclose)
        client = cls(http_client=http)
        client._stack.push_async_callback(stack.pop_all().aclose)  # the client, then the app
        return client
    raise AssertionError("unreachable")  # pragma: no cover


class _PortalTransport(httpx.BaseTransport):
    """A sync transport onto the async app, through the portal thread that runs it."""

    def __init__(self, portal: Any, app: Any) -> None:
        self._portal = portal
        self._asgi = httpx.ASGITransport(app=app)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        request.read()  # now a ByteStream, which iterates for the async side too

        async def send() -> tuple[int, list[tuple[bytes, bytes]], bytes]:
            answer = await self._asgi.handle_async_request(request)
            return answer.status_code, answer.headers.raw, await answer.aread()

        status, headers, content = self._portal.call(send)
        return httpx.Response(status, headers=headers, content=content, request=request)


def embedded_sync(
    cls: "type[Steward]",
    database_url: str,
    *,
    namespace: str = "default",
    review: bool = False,
    settings: dict[str, Any] | None = None,
    app_overrides: dict[str, Any] | None = None,
) -> "Steward":
    from anyio.from_thread import start_blocking_portal

    config = _settings(database_url, settings)
    _migrate(config)
    with ExitStack() as stack:
        portal = stack.enter_context(start_blocking_portal())
        running = _running(config, app_overrides, namespace, review)
        app = stack.enter_context(portal.wrap_async_context_manager(running))
        http = httpx.Client(transport=_PortalTransport(portal, app), base_url=_BASE_URL)
        stack.callback(http.close)
        client = cls(http_client=http)
        client._stack.callback(stack.pop_all().close)  # the client, the app, then the portal
        return client
    raise AssertionError("unreachable")  # pragma: no cover
