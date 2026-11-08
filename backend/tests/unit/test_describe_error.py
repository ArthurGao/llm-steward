"""What a failed job or sync records about its error (jobs.error, external_refs.last_error)."""

from llm_steward.errors import ProviderUnavailable
from llm_steward.services.job_queue import describe_error


def test_our_own_errors_keep_their_text() -> None:
    # adapters build these from status codes and ids only (no provider bodies)
    exc = ProviderUnavailable("weknora POST /knowledge-bases/kb-1/knowledge/file: HTTP 502")
    assert describe_error(exc) == f"ProviderUnavailable: {exc.detail}"


def test_anything_else_has_personal_data_redacted() -> None:
    """A driver or library error can echo what it was given (SQL parameters, request bodies)."""
    exc = ValueError("[parameters: ('Call bob@example.com or 13800138000',)]")
    assert describe_error(exc) == (
        "ValueError: [parameters: ('Call [REDACTED:email] or [REDACTED:phone]',)]"
    )


def test_it_stays_short() -> None:
    assert len(describe_error(RuntimeError("x" * 10_000))) <= 500
