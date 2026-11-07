from collections.abc import Callable
from pathlib import Path

import httpx2
import pytest
from fastapi.testclient import TestClient

from llm_steward.config import Settings
from llm_steward.main import create_app

WEKNORA = "http://weknora.test:8080"


def _settings(database_url: str, **overrides: str) -> Settings:
    values: dict[str, str] = {
        "database_url": database_url,
        "memory_provider": "native",
        "kb_provider": "native",
        "weknora_url": WEKNORA,
        **overrides,
    }
    return Settings(_env_file=None, **values)  # type: ignore[call-arg, arg-type]


def _get_health(settings: Settings, http: httpx2.AsyncClient | None = None) -> httpx2.Response:
    with TestClient(create_app(settings, http=http)) as client:
        return client.get("/health")


def _weknora(handler: Callable[[httpx2.Request], httpx2.Response]) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(transport=httpx2.MockTransport(handler))


def test_all_ok_with_native_providers(db_url: str) -> None:
    response = _get_health(_settings(db_url))
    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "ok"
    assert body["components"]["database"]["status"] == "ok"
    assert body["components"]["database"]["name"] in ("sqlite", "postgresql")
    assert body["components"]["memory_provider"] == {
        "name": "native",
        "status": "ok",
        "detail": "builtin",
    }
    assert body["components"]["kb_provider"]["status"] == "ok"


def test_database_down_returns_503(unreachable_db_url: str) -> None:
    response = _get_health(_settings(unreachable_db_url))
    body = response.json()
    assert response.status_code == 503
    assert body["status"] == "error"
    assert body["components"]["database"]["status"] == "error"
    assert body["components"]["database"]["name"] == "postgresql"


def test_weknora_up_and_mem0_not_implemented_is_degraded(db_url: str) -> None:
    seen: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(str(request.url))
        return httpx2.Response(200, json={"status": "ok"})

    settings = _settings(db_url, memory_provider="mem0", kb_provider="weknora")
    response = _get_health(settings, _weknora(handler))
    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "degraded"
    assert seen == [f"{WEKNORA}/health"]
    assert body["components"]["kb_provider"] == {"name": "weknora", "status": "ok", "detail": None}
    assert body["components"]["memory_provider"]["status"] == "not_implemented"


def _server_error(request: httpx2.Request) -> httpx2.Response:
    return httpx2.Response(500)


def _timeout(request: httpx2.Request) -> httpx2.Response:
    raise httpx2.ReadTimeout("slow", request=request)


def _refused(request: httpx2.Request) -> httpx2.Response:
    raise httpx2.ConnectError("refused", request=request)


@pytest.mark.parametrize(
    ("handler", "detail"),
    [
        (_server_error, "HTTP 500"),
        (_timeout, "timeout after 0.8s"),
        (_refused, "ConnectError: refused"),
    ],
    ids=["http-500", "timeout", "connect-error"],
)
def test_weknora_failures_are_reported(
    tmp_path: Path, handler: Callable[[httpx2.Request], httpx2.Response], detail: str
) -> None:
    settings = _settings(f"sqlite+aiosqlite:///{tmp_path}/h.db", kb_provider="weknora")
    response = _get_health(settings, _weknora(handler))
    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "degraded"
    assert body["components"]["kb_provider"] == {
        "name": "weknora",
        "status": "error",
        "detail": detail,
    }


def test_ragflow_is_reported_not_implemented(tmp_path: Path) -> None:
    settings = _settings(f"sqlite+aiosqlite:///{tmp_path}/h.db", kb_provider="ragflow")
    body = _get_health(settings).json()
    assert body["status"] == "degraded"
    assert body["components"]["kb_provider"] == {
        "name": "ragflow",
        "status": "not_implemented",
        "detail": "adapter arrives in P7",
    }
