from collections.abc import Callable
from pathlib import Path

import httpx2
import pytest
from fastapi.testclient import TestClient

from llm_steward.config import Settings
from llm_steward.llm.roles import LLMConfigError
from llm_steward.main import create_app
from tests.conftest import test_settings
from tests.fakes.embedder import FakeEmbedder
from tests.fakes.http import mock_http

WEKNORA = "http://weknora.test:8080"


def _settings(database_url: str, **overrides: str) -> Settings:
    """Native providers in TEST_SCHEMA (their health checks touch the database)."""
    return test_settings(database_url, weknora_url=WEKNORA, **overrides)


def _get_health(settings: Settings, http: httpx2.AsyncClient | None = None) -> httpx2.Response:
    with TestClient(create_app(settings, http=http, embedder=FakeEmbedder())) as client:
        return client.get("/health")


def test_all_ok_with_native_providers(migrated_url: str) -> None:
    response = _get_health(_settings(migrated_url))
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


def test_weknora_up_is_reported_ok(migrated_url: str) -> None:
    # Was "…and mem0 not implemented": mem0 is implemented since P2, so the memory side now uses
    # native here; startup validation of mem0 is covered by the test below.
    seen: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(str(request.url))
        return httpx2.Response(200, json={"status": "ok"})

    settings = _settings(migrated_url, kb_provider="weknora")
    response = _get_health(settings, mock_http(handler))
    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "ok"
    assert seen == [f"{WEKNORA}/health"]
    assert body["components"]["kb_provider"] == {"name": "weknora", "status": "ok", "detail": None}


def test_mem0_without_an_llm_for_its_roles_refuses_to_start(migrated_url: str) -> None:
    """KB llm-app-architecture §1.5: a role that cannot be served fails at startup, not at the
    first extraction."""
    settings = _settings(migrated_url, memory_provider="mem0")
    with pytest.raises(LLMConfigError, match=r"memory\.extract"):
        _get_health(settings)


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
    response = _get_health(settings, mock_http(handler))
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


LITERAG = "http://literag.test:8700"


def test_literag_over_http_is_probed_at_its_url(tmp_path: Path) -> None:
    seen: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(str(request.url))
        return httpx2.Response(200, json={"status": "ok"})

    settings = _settings(
        f"sqlite+aiosqlite:///{tmp_path}/h.db", kb_provider="literag", literag_url=LITERAG
    )
    body = _get_health(settings, mock_http(handler)).json()
    assert body["components"]["kb_provider"] == {"name": "literag", "status": "ok", "detail": None}
    assert seen == [f"{LITERAG}/health"]


def test_literag_down_is_reported(tmp_path: Path) -> None:
    settings = _settings(
        f"sqlite+aiosqlite:///{tmp_path}/h.db", kb_provider="literag", literag_url=LITERAG
    )
    body = _get_health(settings, mock_http(_refused)).json()
    assert body["status"] == "degraded"
    assert body["components"]["kb_provider"] == {
        "name": "literag",
        "status": "error",
        "detail": "lite-rag GET /health: ConnectError",
    }


def test_literag_in_process_is_built_from_its_own_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from lite_rag.engine import RagRuntime

    built: list[object] = []

    class Runtime:
        closed = False

        def health(self) -> None:
            pass

        def close(self) -> None:
            Runtime.closed = True

    def from_settings(settings: object, **kwargs: object) -> Runtime:
        built.append(settings)
        return Runtime()

    config = tmp_path / "literag.yaml"
    config.write_text("store: {schema: from_file}\n")
    monkeypatch.setattr(RagRuntime, "from_settings", from_settings)
    settings = _settings(
        f"sqlite+aiosqlite:///{tmp_path}/h.db",
        kb_provider="literag",
        literag_mode="inprocess",
        literag_config=str(config),
    )
    body = _get_health(settings).json()
    assert body["components"]["kb_provider"]["status"] == "ok"
    [lite_settings] = built
    assert lite_settings.store.schema_name == "from_file"  # type: ignore[attr-defined]
    assert Runtime.closed  # the app's shutdown released lite-rag's connections
