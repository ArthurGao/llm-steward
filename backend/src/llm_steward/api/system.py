from dataclasses import asdict

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from llm_steward.services.health_service import check_health

router = APIRouter(tags=["system"])


@router.get("/health")
async def health(request: Request) -> JSONResponse:
    report = await check_health(request.app.state.engine, request.app.state.providers)
    return JSONResponse(asdict(report), status_code=503 if report.status == "error" else 200)
