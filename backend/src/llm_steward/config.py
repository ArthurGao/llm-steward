"""Runtime settings, read from environment variables with the STEWARD_ prefix."""

from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MemoryProviderName = Literal["mem0", "native"]
KnowledgeProviderName = Literal["weknora", "ragflow", "native"]

DEFAULT_DB_SCHEMA = "steward"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="STEWARD_", env_file=".env", extra="ignore")

    # Required on purpose: a default would silently create a SQLite file in whatever directory the
    # command happens to run from (e.g. the repo root, whose .env has no STEWARD_DATABASE_URL).
    database_url: str
    # PostgreSQL schema holding the governance tables; ignored on SQLite.
    # Restricted to a plain identifier because it names a schema in DDL and search_path.
    db_schema: str = Field(default=DEFAULT_DB_SCHEMA, pattern=r"^[a-z_][a-z0-9_]{0,62}$")

    memory_provider: MemoryProviderName = "mem0"
    kb_provider: KnowledgeProviderName = "weknora"
    weknora_url: str = "http://localhost:8680"
    weknora_api_key: str | None = None
    provider_timeout_ms: int = Field(default=800, gt=0)

    llm_model: str = "anthropic/claude-haiku-5-5"

    @field_validator("db_schema")
    @classmethod
    def _not_a_test_schema(cls, value: str) -> str:
        # Test fixtures DROP ... CASCADE schemas named *_test, so no app process may ever use one.
        if value.endswith("_test"):
            raise ValueError(f"schema names ending in _test are reserved for tests: {value!r}")
        return value
