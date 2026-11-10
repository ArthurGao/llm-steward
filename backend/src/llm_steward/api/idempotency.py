"""`Idempotency-Key` on `/v1` writes (design §7), as ASGI middleware: it needs the whole response.

Only requests that carry the header are touched. The caller is resolved by the same step the
routes use (`deps.resolve_principal`: authenticated and charged to its rate limit once, then
cached for the route), so a throttled request is refused before any key is reserved. An
unauthenticated request passes through and gets its 401 from the route. The key is scoped to the
caller and to the exact request; the storage rules are in `services/idempotency_service.py`.
"""

import re
import uuid

from fastapi import HTTPException
from starlette.datastructures import Headers
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from llm_steward.api.deps import principal_from_scope
from llm_steward.api.errors import http_problem, problem
from llm_steward.errors import StewardError
from llm_steward.services.container import Services
from llm_steward.services.idempotency_service import StoredResponse, principal_ref, request_hash

WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
HEADER = "idempotency-key"
_VALID_KEY = re.compile(r"[\x21-\x7e]{1,200}")  # visible ASCII, like a UUID or a ULID
# Not answers to the request but "try again": the key is released, as for a server error.
_RETRYABLE = frozenset({408, 429})
# Never stored: a minted user token would sit in the table as a live credential, and a retrieval's
# answer is memory content. Neither needs replaying (minting twice is harmless; reads write
# nothing), so the header is ignored on them.
NOT_STORED = frozenset({"/v1/tokens/user", "/v1/retrieve"})


class _TooLarge(Exception):
    pass


class IdempotencyMiddleware:
    def __init__(self, app: ASGIApp, *, max_body: int) -> None:
        self.app = app
        self.max_body = max_body  # buffered to hash it: no larger than any route accepts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] not in WRITE_METHODS
            or not scope["path"].startswith("/v1/")
            or scope["path"] in NOT_STORED
        ):
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        key = headers.get(HEADER)
        if key is None:
            await self.app(scope, receive, send)
            return
        if not _VALID_KEY.fullmatch(key):
            detail = "Idempotency-Key must be 1 to 200 visible ASCII characters"
            await problem(400, "invalid_idempotency_key", detail)(scope, receive, send)
            return
        services: Services = scope["app"].state.services
        try:
            principal = await principal_from_scope(services, scope)
        except HTTPException as exc:
            if exc.status_code == 401:  # the route answers it, exactly as without the header
                await self.app(scope, receive, send)
                return
            await http_problem(exc)(scope, receive, send)
            return
        try:
            body = await _read_body(receive, self.max_body)
        except _TooLarge:
            await problem(413, "too_large", "request body too large")(scope, receive, send)
            return
        digest = request_hash(
            scope["method"],
            scope["path"],
            scope["query_string"].decode("latin-1"),
            headers.get("content-type", ""),
            body,
        )
        try:
            outcome = await services.idempotency.reserve(
                principal.namespace_id, principal_ref(principal), key, digest
            )
        except StewardError as exc:
            await problem(exc.status, exc.code, exc.detail)(scope, receive, send)
            return
        if isinstance(outcome, StoredResponse):
            replay = Response(
                outcome.body,
                outcome.status_code,
                headers={"Idempotent-Replayed": "true"},
                media_type=outcome.content_type,
            )
            await replay(scope, receive, send)
            return
        await self._run(scope, receive, send, body, outcome, services)

    async def _run(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        body: bytes,
        reservation: uuid.UUID,
        services: Services,
    ) -> None:
        status: int | None = None
        content_type: str | None = None
        chunks: list[bytes] = []
        pending = [{"type": "http.request", "body": body, "more_body": False}]

        async def replay_body() -> Message:
            return pending.pop() if pending else await receive()  # then e.g. a disconnect

        async def capture(message: Message) -> None:
            nonlocal status, content_type
            if message["type"] == "http.response.start":
                status = message["status"]
                content_type = Headers(raw=message.get("headers", [])).get("content-type")
            elif message["type"] == "http.response.body":
                chunks.append(message.get("body", b""))
            await send(message)

        try:
            await self.app(scope, replay_body, capture)
        except BaseException:
            await services.idempotency.release(reservation)
            raise
        if status is None or status >= 500 or status in _RETRYABLE:
            await services.idempotency.release(reservation)
        else:
            await services.idempotency.complete(reservation, status, content_type, b"".join(chunks))


async def _read_body(receive: Receive, limit: int) -> bytes:
    parts: list[bytes] = []
    size = 0
    while True:
        message = await receive()
        if message["type"] != "http.request":
            break
        chunk = message.get("body", b"")
        size += len(chunk)
        if size > limit:
            raise _TooLarge
        parts.append(chunk)
        if not message.get("more_body", False):
            break
    return b"".join(parts)
