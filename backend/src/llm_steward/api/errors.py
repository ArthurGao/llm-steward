"""RFC 9457 Problem Details for every error response: {type, title, status, detail, code}."""

from collections.abc import Mapping
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from llm_steward.errors import StewardError

PROBLEM_JSON = "application/problem+json"


def problem(
    status: int, code: str, detail: str, headers: Mapping[str, str] | None = None, **extra: Any
) -> JSONResponse:
    body = {
        "type": f"urn:llm-steward:problem:{code}",
        "title": HTTPStatus(status).phrase,
        "status": status,
        "detail": detail,
        "code": code,
        **extra,
    }
    return JSONResponse(body, status_code=status, media_type=PROBLEM_JSON, headers=headers)


_HTTP_CODES = {
    401: "unauthenticated",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
}


def install(app: FastAPI) -> None:
    @app.exception_handler(StewardError)
    async def _domain(_: Request, exc: StewardError) -> JSONResponse:
        return problem(exc.status, exc.code, exc.detail)

    @app.exception_handler(HTTPException)
    async def _http(_: Request, exc: HTTPException) -> JSONResponse:
        code = _HTTP_CODES.get(exc.status_code, "http_error")
        return problem(exc.status_code, code, str(exc.detail), headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {"loc": list(e.get("loc", ())), "msg": e.get("msg", ""), "type": e.get("type", "")}
            for e in exc.errors()
        ]
        return problem(422, "validation_error", "request validation failed", errors=errors)
