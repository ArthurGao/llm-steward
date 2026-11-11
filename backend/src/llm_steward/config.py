"""Runtime settings, read from environment variables with the STEWARD_ prefix."""

from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MemoryProviderName = Literal["mem0", "native", "litemem"]
KnowledgeProviderName = Literal["weknora", "ragflow", "native", "literag"]
# lite-rag's retrieval modes in the pinned version (S10). Its S11 adds "graph": add it here when
# the pin moves past S11 (the graph also needs lite-rag kbs built with it).
LiteRagSearchMode = Literal["vector", "bm25", "hybrid"]

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
    # RAGFlow (complex documents, profile `docparse`); its embedding model is the tenant default
    # that deploy/ragflow/bootstrap.py sets, and it prints the key.
    ragflow_url: str = "http://127.0.0.1:8690"
    ragflow_api_key: str | None = None
    # The built-in parser new datasets use: general, paper, table, laws, book, manual, … (RAGFlow's
    # reason to be the complex-documents engine; existing datasets keep theirs)
    ragflow_parser_id: str = "general"
    # lite-rag (the owner's own RAG engine): its HTTP service by default, or in-process (extra
    # `literag`, configured by its own LITERAG_* variables or `literag_config`, a lite-rag YAML).
    literag_mode: Literal["http", "inprocess"] = "http"
    literag_url: str = "http://127.0.0.1:8700"
    literag_api_key: str | None = None  # a lite-rag key whose namespaces cover `<prefix>/*`
    literag_namespace_prefix: str = Field(default="steward", pattern=r"^[a-z0-9][a-z0-9_.-]{0,62}$")
    literag_config: str | None = None
    # How lite-rag searches for llm-steward, sent with every search so that changing lite-rag's own
    # defaults never changes what llm-steward retrieves. The defaults are what lite-rag's own
    # defaults gave before. Rerank stays off: P4 scores and reranks the merged hits itself.
    literag_search_mode: LiteRagSearchMode = "hybrid"
    literag_rerank: bool = False
    # lite-mem (the owner's own memory engine): its HTTP service by default, or in-process (extra
    # `litemem`, configured by its own LITEMEM_* variables or `litemem_config`, an env file). Never
    # run lite-mem's `maintain` on these namespaces: llm-steward owns validity and review.
    litemem_mode: Literal["http", "inprocess"] = "http"
    litemem_url: str = "http://127.0.0.1:8710"
    litemem_api_key: str | None = None  # a lite-mem key whose namespaces cover `<prefix>/*`
    litemem_namespace_prefix: str = Field(default="steward", pattern=r"^[a-z0-9][a-z0-9_.-]{0,62}$")
    litemem_config: str | None = None
    # One lite-mem call: an inferred add runs its extraction and resolution models, seconds to
    # tens of seconds on a local model. The worker's per-job limit (30 s) still applies, and
    # retrieval keeps its own `retrieve_timeout_ms`.
    litemem_timeout_s: float = Field(default=30.0, gt=0)
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
    # Memory extraction by llm-steward (role memory.extract, then memory.resolve) for an engine that
    # cannot extract (native): conversations then become memories without mem0. Off: such an
    # engine takes direct writes only (POST /v1/memories). An engine that extracts ignores it.
    memory_llm: bool = False
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
    # User tokens (design §7, P5): `POST /v1/tokens/user` signs short-lived HS256 tokens with
    # this secret (at least 32 bytes, e.g. `openssl rand -hex 32`). Unset: none issued or accepted.
    token_secret: SecretStr | None = Field(default=None, min_length=32)
    # The Admin (`/admin/v1`, design §9): no login and no accounts (decided 2026-11-10); a static
    # bearer token, at least 32 bytes. Unset: the Admin API is off (503), never open.
    admin_token: SecretStr | None = Field(default=None, min_length=32)
    # Browser origins allowed to call the API cross-origin (e.g. the Admin run locally against a
    # remote backend, or deployed on its own). JSON list in env. Empty: same origin only.
    cors_origins: list[str] = Field(default_factory=list)
    # Requests per minute per API key (a user token spends its key's), in this process; a
    # namespace may set its own (`limits.requests_per_minute`). None: unlimited.
    rate_limit_per_minute: int | None = Field(default=600, ge=1)
    # Webhooks and document URLs are user-supplied: only public https addresses are called.
    # Local development only: also private addresses and plain http (e.g. a receiver on localhost).
    allow_private_urls: bool = False

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
