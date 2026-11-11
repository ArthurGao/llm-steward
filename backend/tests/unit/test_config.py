import pytest
from pydantic import ValidationError

from llm_steward.config import Settings

DB = "sqlite+aiosqlite:///./x.db"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("STEWARD_DATABASE_URL", "STEWARD_MEMORY_PROVIDER", "STEWARD_KB_PROVIDER"):
        monkeypatch.delenv(key, raising=False)


def test_defaults() -> None:
    s = Settings(_env_file=None, database_url=DB)  # type: ignore[call-arg]
    assert s.db_schema == "steward"
    assert (s.memory_provider, s.kb_provider) == ("mem0", "weknora")
    assert s.weknora_url == "http://localhost:8680"
    assert s.provider_timeout_ms == 800


def test_database_url_is_required() -> None:
    with pytest.raises(ValidationError, match="database_url"):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_reads_steward_prefixed_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STEWARD_DATABASE_URL", DB)
    monkeypatch.setenv("STEWARD_KB_PROVIDER", "ragflow")
    monkeypatch.setenv("STEWARD_PROVIDER_TIMEOUT_MS", "1500")
    monkeypatch.setenv("KB_PROVIDER", "native")  # unprefixed: must be ignored
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.database_url == DB
    assert s.kb_provider == "ragflow"
    assert s.provider_timeout_ms == 1500


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("STEWARD_MEMORY_PROVIDER", "letta"),
        ("STEWARD_KB_PROVIDER", "elasticsearch"),
        ("STEWARD_DB_SCHEMA", 'steward"; drop table x; --'),
        ("STEWARD_DB_SCHEMA", "steward_test"),  # reserved: the test fixtures drop *_test schemas
        ("STEWARD_PROVIDER_TIMEOUT_MS", "0"),
    ],
)
def test_rejects_invalid_values(monkeypatch: pytest.MonkeyPatch, key: str, value: str) -> None:
    monkeypatch.setenv(key, value)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url=DB)  # type: ignore[call-arg]


def test_the_image_bakes_in_the_default_embedding_model() -> None:
    """The Dockerfile downloads the embedding model at build time; a different default here would
    make every container download its model on first use again."""
    from pathlib import Path

    dockerfile = (Path(__file__).parents[2] / "Dockerfile").read_text()
    [arg] = [line for line in dockerfile.splitlines() if line.startswith("ARG EMBEDDING_MODEL=")]
    default = Settings(_env_file=None, database_url=DB).embedding_model  # type: ignore[call-arg]
    assert arg.removeprefix("ARG EMBEDDING_MODEL=") == default
