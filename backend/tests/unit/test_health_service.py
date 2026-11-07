import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from llm_steward.providers.base import ComponentHealth
from llm_steward.services import health_service
from llm_steward.services.health_service import check_health
from llm_steward.storage.db import engine_scope


class _Healthy:
    name = "native"

    async def health(self) -> ComponentHealth:
        return ComponentHealth(self.name, "ok", "builtin")


class _Exploding:
    name = "exploding"

    async def health(self) -> ComponentHealth:
        raise RuntimeError("adapter bug")


class _Hanging:
    name = "hanging"

    async def health(self) -> ComponentHealth:
        await asyncio.sleep(10)
        return ComponentHealth(self.name, "ok")


@pytest.fixture
async def engine(tmp_path: Path) -> AsyncIterator[AsyncEngine]:
    async with engine_scope(f"sqlite+aiosqlite:///{tmp_path / 'h.db'}", schema="unused") as e:
        yield e


async def test_a_probe_that_raises_is_reported_not_propagated(engine: AsyncEngine) -> None:
    report = await check_health(engine, {"kb_provider": _Exploding()})
    assert report.status == "degraded"
    assert report.components["kb_provider"] == ComponentHealth(
        "exploding", "error", "RuntimeError: adapter bug"
    )


async def test_a_hanging_probe_times_out(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(health_service, "PROBE_TIMEOUT_SECONDS", 0.05)
    report = await asyncio.wait_for(
        check_health(engine, {"memory_provider": _Healthy(), "kb_provider": _Hanging()}),
        timeout=2,
    )
    assert report.status == "degraded"
    assert report.components["kb_provider"] == ComponentHealth(
        "hanging", "error", "timeout after 0.05s"
    )
    assert report.components["memory_provider"].status == "ok"
