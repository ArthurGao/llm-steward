"""Aggregate health of the governance store and the configured providers."""

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncEngine

from llm_steward.providers.base import ComponentHealth, HealthProbe
from llm_steward.storage.db import ping

OverallStatus = Literal["ok", "degraded", "error"]

# Keeps a dead database from outlasting the 4 s Docker HEALTHCHECK request.
DATABASE_TIMEOUT_SECONDS = 3.0


@dataclass(frozen=True)
class HealthReport:
    status: OverallStatus
    components: dict[str, ComponentHealth]


async def check_health(engine: AsyncEngine, providers: Mapping[str, HealthProbe]) -> HealthReport:
    database, *provider_health = await asyncio.gather(
        _check_database(engine), *(p.health() for p in providers.values())
    )
    components = {"database": database, **dict(zip(providers, provider_health, strict=True))}

    if database.status != "ok":
        status: OverallStatus = "error"
    elif all(c.status == "ok" for c in provider_health):
        status = "ok"
    else:
        status = "degraded"
    return HealthReport(status=status, components=components)


async def _check_database(engine: AsyncEngine) -> ComponentHealth:
    name = engine.dialect.name
    try:
        async with asyncio.timeout(DATABASE_TIMEOUT_SECONDS):
            await ping(engine)
    except TimeoutError:
        return ComponentHealth(name, "error", f"timeout after {DATABASE_TIMEOUT_SECONDS:g}s")
    except Exception as exc:
        return ComponentHealth.from_exception(name, exc)
    return ComponentHealth(name, "ok")
