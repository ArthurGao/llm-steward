"""Errors raised by the client. Every API error is RFC 9457 Problem Details on the wire; it comes
back as a `StewardError` subclass carrying its `status`, stable `code` and `detail`."""

from typing import Any


class StewardError(Exception):
    """Base class. `code` is the server's machine-readable identifier (e.g. `not_found`)."""

    def __init__(
        self, status: int, code: str, detail: str, body: dict[str, Any] | None = None
    ) -> None:
        super().__init__(f"{status} {code}: {detail}")
        self.status, self.code, self.detail = status, code, detail
        self.body = body or {}


class StewardConnectionError(StewardError):
    """The server could not be reached (after the retries)."""


class AuthenticationError(StewardError):
    """401: a missing, wrong, expired or revoked API key or user token. Never retried."""


class PermissionDeniedError(StewardError):
    """403: the credential may not do this (a scope, a user token's reach). Never retried."""


class NotFoundError(StewardError):
    """404 (also: another namespace's or another user's object)."""


class ConflictError(StewardError):
    """409: e.g. an illegal state change, or the same Idempotency-Key still in flight."""


class UnprocessableError(StewardError):
    """422: a well-formed request that cannot be served (validation, a missing capability)."""


class RateLimitError(StewardError):
    """429 after the retries; `retry_after` is the server's hint in seconds."""

    retry_after: float | None = None


class ServerError(StewardError):
    """5xx after the retries."""


_BY_STATUS: dict[int, type[StewardError]] = {
    401: AuthenticationError,
    403: PermissionDeniedError,
    404: NotFoundError,
    409: ConflictError,
    422: UnprocessableError,
    429: RateLimitError,
}


def error_for(status: int, body: Any) -> StewardError:
    problem = body if isinstance(body, dict) else {}
    code = str(problem.get("code") or "http_error")
    detail = str(problem.get("detail") or problem.get("title") or f"HTTP {status}")
    kind = _BY_STATUS.get(status) or (ServerError if status >= 500 else StewardError)
    return kind(status, code, detail, problem)
