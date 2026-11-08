"""Domain errors raised by services and providers; the API layer maps them to RFC 9457 responses."""


class StewardError(Exception):
    """Base class. `code` is the stable machine-readable identifier sent to clients."""

    status = 500
    code = "internal_error"

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class NotFound(StewardError):
    status = 404
    code = "not_found"


class Forbidden(StewardError):
    """The caller may not do this (e.g. only a reviewer can make knowledge verified)."""

    status = 403
    code = "forbidden"


class Conflict(StewardError):
    """E.g. an illegal state transition."""

    status = 409
    code = "conflict"


class Unprocessable(StewardError):
    """A well-formed request the configured providers cannot serve (e.g. a missing capability)."""

    status = 422
    code = "unprocessable"


class ProviderUnavailable(StewardError):
    """Every provider needed for the request failed or is not implemented yet."""

    status = 503
    code = "provider_unavailable"
