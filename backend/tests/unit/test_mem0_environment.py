"""The provider prepares mem0's import-time environment itself (without importing mem0 here)."""

from pathlib import Path

import pytest

from llm_steward.providers.mem0_provider import mem0_connection_string, prepare_mem0_environment


def test_telemetry_off_home_dir_moved_cache_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for var in ("MEM0_TELEMETRY", "MEM0_DIR", "FASTEMBED_CACHE_PATH"):
        monkeypatch.delenv(var, raising=False)
    prepare_mem0_environment(str(tmp_path / "data" / "history.db"), str(tmp_path / "models"))
    import os

    assert os.environ["MEM0_TELEMETRY"] == "false"
    assert os.environ["MEM0_DIR"] == str(tmp_path / "data")
    assert (tmp_path / "data").is_dir()
    assert os.environ["FASTEMBED_CACHE_PATH"] == str(tmp_path / "models")


def test_telemetry_is_forced_off_even_if_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEM0_TELEMETRY", "true")
    prepare_mem0_environment(str(tmp_path / "h.db"), None)
    import os

    assert os.environ["MEM0_TELEMETRY"] == "false"


def test_connection_string_puts_mem0_in_its_schema() -> None:
    url = mem0_connection_string("postgresql+asyncpg://u:p%40ss@db:5432/llm_steward", "mem0")
    assert url == (
        "postgresql://u:p%40ss@db:5432/llm_steward?options=-csearch_path%3Dmem0%2Cpublic"
    )
