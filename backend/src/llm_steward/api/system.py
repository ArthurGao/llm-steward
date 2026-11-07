from dataclasses import asdict

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from llm_steward.api.deps import ServicesDep
from llm_steward.services.health_service import check_health

router = APIRouter(tags=["system"])


@router.get("/health")
async def health(services: ServicesDep) -> JSONResponse:
    """Unauthenticated (see deps.UNAUTHENTICATED_PATHS): component status only, no data."""
    report = await check_health(services.engine, services.providers.by_role())
    return JSONResponse(asdict(report), status_code=503 if report.status == "error" else 200)
