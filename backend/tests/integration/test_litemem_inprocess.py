"""lite-mem in-process (the default memory engine since 2026-11-12) beside a SQLite governance
database: with no database of its own configured it keeps a SQLite file in llm-steward's data
directory. Real lite-mem library; a known embedding size, so no model is called."""

import uuid
from pathlib import Path

import httpx2

from llm_steward.providers.base import Scope
from llm_steward.providers.registry import build_litemem
from tests.conftest import test_settings


async def test_beside_sqlite_lite_mem_keeps_a_file_in_the_data_dir(tmp_path: Path) -> None:
    own = tmp_path / "litemem.env"
    own.write_text("LITEMEM_EMBEDDING__DIM=16\n")
    settings = test_settings(
        f"sqlite+aiosqlite:///{tmp_path / 'steward.db'}", memory_provider="litemem",
        litemem_mode="inprocess", litemem_config=str(own), data_dir=str(tmp_path / "data"),
    )  # fmt: skip
    async with httpx2.AsyncClient() as http:
        provider = build_litemem(settings, http)
        try:
            assert await provider.list(Scope(uuid.uuid4(), "u1")) == []
        finally:
            await provider.aclose()
    assert (tmp_path / "data" / "litemem.db").is_file()
