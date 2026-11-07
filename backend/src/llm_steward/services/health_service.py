"""Aggregate health of the governance store and the configured providers."""

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncEngine

from llm_steward.providers.base import ComponentHealth, HealthProbe
from llm_steward.storage.db import ping

OverallStatus = Literal["ok", "degraded", "error"]

# Both stay under the 4 s Docker HEALTHCHECK request, so /health always answers with a report.
DATABASE_TIMEOUT_SECONDS = 3.0
PROBE_TIMEOUT_SECONDS = 3.0


@dataclass(frozen=True)
class HealthReport:
    status: OverallStatus
    components: dict[str, ComponentHealth]


async def check_health(engine: AsyncEngine, providers: Mapping[str, HealthProbe]) -> HealthReport:
    database, *provider_health = await asyncio.gather(
        _bounded(engine.dialect.name, _database_ok(engine), DATABASE_TIMEOUT_SECONDS),
        *(_bounded(p.name, p.health, PROBE_TIMEOUT_SECONDS) for p in providers.values()),
    )
    components = {"database": database, **dict(zip(providers, provider_health, strict=True))}

    if database.status != "ok":
        status: OverallStatus = "error"
    elif all(c.status == "ok" for c in provider_health):
        status = "ok"
    else:
        status = "degraded"
    return HealthReport(status=status, components=components)


def _database_ok(engine: AsyncEngine) -> Callable[[], Awaitable[ComponentHealth]]:
    async def check() -> ComponentHealth:
        await ping(engine)
        return ComponentHealth(engine.dialect.name, "ok")

    return check


async def _bounded(
    name: str, check: Callable[[], Awaitable[ComponentHealth]], timeout_s: float
) -> ComponentHealth:
    """Run one check so that a hang or an unexpected exception becomes that component's error."""
    try:
        async with asyncio.timeout(timeout_s):
            return await check()
    except TimeoutError:
        return ComponentHealth(name, "error", f"timeout after {timeout_s:g}s")
    except Exception as exc:
        return ComponentHealth.from_exception(name, exc)
