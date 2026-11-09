"""Runtime settings, read from environment variables with the STEWARD_ prefix."""

from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MemoryProviderName = Literal["mem0", "native"]
KnowledgeProviderName = Literal["weknora", "ragflow", "native", "literag"]

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
    # The embedding model new WeKnora knowledge bases use (deploy/weknora/bootstrap.py prints it).
    weknora_embedding_model_id: str | None = None
    # lite-rag (the owner's own RAG engine): its HTTP service by default, or in-process (extra
    # `literag`, configured by its own LITERAG_* variables or `literag_config`, a lite-rag YAML).
    literag_mode: Literal["http", "inprocess"] = "http"
    literag_url: str = "http://127.0.0.1:8700"
    literag_api_key: str | None = None  # a lite-rag key whose namespaces cover `<prefix>/*`
    literag_namespace_prefix: str = Field(default="steward", pattern=r"^[a-z0-9][a-z0-9_.-]{0,62}$")
    literag_config: str | None = None
    provider_timeout_ms: int = Field(default=800, gt=0)
    # Per-provider budget for one /v1/retrieve search; a slower provider is reported as degraded.
    retrieve_timeout_ms: int = Field(default=800, gt=0)

    # LLM role layer (llm/roles.py). Call sites name a role; these say which endpoint and model
    # serve it. Dev: llm-gateway (http://127.0.0.1:8787/v1, gpt-sub / claude-sub). Prod: a vendor.
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None  # default model for every role
    llm_roles: dict[str, str] = Field(default_factory=dict)  # role -> model override (JSON in env)
    llm_timeout_s: float = Field(default=60.0, gt=0)
    # KB pipeline LLM steps (design §5): risk labels, worthiness scoring, drafting (roles kb.risk,
    # kb.worthiness, kb.draft). Off: rules-only risk, no suggestions; promote/suggestions → 422.
    kb_llm: bool = False
    # KB document uploads (P3d): the bytes wait in the governance DB until the KB provider has them.
    kb_max_upload_mb: int = Field(default=20, gt=0, le=50)  # WeKnora v0.9.0 accepts up to 50

    # Episode buffer (design §5 step 1): flush past this many tokens, or after this long idle.
    buffer_tokens: int = Field(default=1024, gt=0)
    buffer_idle_minutes: int = Field(default=30, gt=0)
    # Daily drift check between a memory provider and the mirror.
    reconcile_interval_hours: int = Field(default=24, gt=0)
    # Local state some engines keep on disk (mem0's history database).
    data_dir: str = "./data"

    # Native provider only: local fastembed model (downloaded once, then offline) and its cache.
    embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    model_cache_dir: str | None = None
    # Optional rerank (design §6 step 5; extra `rerank`): a local fastembed cross-encoder, e.g.
    # BAAI/bge-reranker-v2-m3-int8 (Apache-2.0, multilingual, 0.57 GB). Unset: `rerank: true` is
    # refused. Its own budget: a cross-encoder over 30 items takes about a second on a laptop.
    rerank_model: str | None = None
    rerank_timeout_ms: int = Field(default=3000, gt=0)
    # Run the outbox worker inside the API process (single-process deployments); otherwise run
    # `steward worker` separately.
    run_worker: bool = True

    @field_validator("db_schema")
    @classmethod
    def _not_a_test_schema(cls, value: str) -> str:
        # Test fixtures DROP ... CASCADE schemas named *_test, so no app process may ever use one.
        if value.endswith("_test"):
            raise ValueError(f"schema names ending in _test are reserved for tests: {value!r}")
        return value
