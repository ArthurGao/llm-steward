"""Runtime settings, read from environment variables with the STEWARD_ prefix."""

from typing import Annotated, Any, Literal

from lite_model import ModelsConfig
from pydantic import BaseModel, BeforeValidator, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MemoryProviderName = Literal["mem0", "native", "litemem"]
KnowledgeProviderName = Literal["weknora", "ragflow", "native", "literag"]
# lite-rag's retrieval modes. `graph` (lite-rag S11): the entities the query names, then the chunks
# that mention them and their neighbours; it needs lite-rag kbs built with `graph.enabled`
# (PostgreSQL), and `hybrid` fuses it too when lite-rag's own `retrieval.hybrid_paths` lists it.
LiteRagSearchMode = Literal["vector", "bm25", "graph", "hybrid"]

DEFAULT_DB_SCHEMA = "steward"


class LLMConfig(ModelsConfig):
    """lite-model's configuration for llm-steward's roles (lite-model design §4): `backend`,
    `model`, `roles` (per role: `model`, `backend`, …), `backends`, `max_output_tokens`, … — in env
    `STEWARD_LLM__BACKEND`, `STEWARD_LLM__MODEL`, `STEWARD_LLM__ROLES` (JSON), …. No default model:
    a role that will be used and is not configured fails at startup. The timeout and the two
    retries are what llm-steward's own OpenAI client had."""

    timeout_s: float = Field(default=60.0, gt=0)
    max_retries: int = Field(default=2, ge=0)


def _moved(value: Any) -> None:
    if value not in (None, ""):  # an empty one, as an old .env.example left it, is unset
        raise ValueError(
            "the LLM settings moved to lite-model's shape: STEWARD_LLM__BACKEND, "
            "STEWARD_LLM__MODEL, STEWARD_LLM__BACKENDS (an endpoint and its key's variable), "
            "STEWARD_LLM__ROLES, STEWARD_LLM__TIMEOUT_S (README, .env.example)"
        )


# STEWARD_LLM_BASE_URL and the rest, before lite-model: refused, never silently ignored
Moved = Annotated[None, BeforeValidator(_moved)]


class CacheSettings(BaseModel):
    """lite-cache's exact (L1) LLM response cache for the KB labels (design §11 P10): a repeated
    kb.risk / kb.worthiness request (same role, model, prompt and text) in the same namespace is
    answered from PostgreSQL. Off by default; PostgreSQL only (lite-cache's store)."""

    enabled: bool = False
    # How long an answer is reused; None: until invalidated. The key already changes with the
    # model, the prompt and the text, so this only bounds a model behaving differently later.
    ttl_s: int | None = Field(default=30 * 86_400, gt=0)
    # lite-cache's own schema in the governance database.
    db_schema: str = Field(default="steward_cache", pattern=r"^[a-z_][a-z0-9_]{0,62}$")


class AgentSettings(BaseModel):
    """The reconcile triage assistant (design §11 P11): after a reconcile finds provider records no
    mirror row points at, a lite-agent tool loop reads around each one and proposes an action
    for a person. Off by default; PostgreSQL only (lite-agent's session store); needs the
    `agent.triage` role served by a model that can call tools."""

    reconcile_triage: bool = False
    max_orphans: int = Field(default=20, ge=1, le=200)  # per reconcile; the rest wait for the next
    turns: int = Field(default=8, ge=2, le=30)  # model calls per orphan
    # lite-agent's own schema (its sessions and events) in the governance database.
    db_schema: str = Field(default="steward_agent", pattern=r"^[a-z_][a-z0-9_]{0,62}$")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="STEWARD_", env_file=".env", env_nested_delimiter="__", extra="ignore"
    )

    # Required on purpose: a default would silently create a SQLite file in whatever directory the
    # command happens to run from (e.g. the repo root, whose .env has no STEWARD_DATABASE_URL).
    database_url: str
    # PostgreSQL schema holding the governance tables; ignored on SQLite.
    # Restricted to a plain identifier because it names a schema in DDL and search_path.
    db_schema: str = Field(default=DEFAULT_DB_SCHEMA, pattern=r"^[a-z_][a-z0-9_]{0,62}$")

    # The memory engine: lite-mem (default since 2026-11-12; extracts and resolves conflicts), mem0
    # (extracts, llm-steward compensates conflicts) or native (llm-steward extracts: memory_llm).
    memory_provider: MemoryProviderName = "litemem"
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
    # `litemem`, in the image; compose runs it so), configured by its own LITEMEM_* variables or
    # `litemem_config` (an env file); in-process it defaults to the governance database (schema
    # lite_mem, migrated at start; beside SQLite, `<data_dir>/litemem.db`). Never
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

    # LLM role layer (llm/roles.py): which model on which backend serves each role. Dev: llm-gateway
    # (backend `gateway`, models gpt-sub / claude-sub). Prod: a vendor. None: no LLM roles.
    llm: LLMConfig | None = None
    llm_base_url: Moved = None
    llm_api_key: Moved = None
    llm_model: Moved = None
    llm_roles: Moved = None
    llm_timeout_s: Moved = None
    # KB pipeline LLM steps (design §5): risk labels, worthiness scoring, drafting (roles kb.risk,
    # kb.worthiness, kb.draft). Off: rules-only risk, no suggestions; promote/suggestions → 422.
    kb_llm: bool = False
    # STEWARD_CACHE__ENABLED=true: the KB labels' LLM answers are cached per namespace (lite-cache).
    cache: CacheSettings = CacheSettings()
    # STEWARD_AGENT__RECONCILE_TRIAGE=true: an agent proposes what to do with unmirrored records.
    agent: AgentSettings = AgentSettings()
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
    # A database that scales to zero (e.g. Neon): the worker stops querying this long after the
    # last request or job and sleeps until a request, the next due job, or the episode buffers'
    # idle flush. Unset: it polls every second. Assumes this API process is the only one that
    # enqueues: a job another process enqueues while it is idle waits for the next request.
    worker_idle_after_s: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _lite_modules_need_their_own_postgresql_schema(self) -> "Settings":
        on_pg = self.database_url.startswith("postgresql")
        for key, enabled, schema in (
            ("CACHE", self.cache.enabled, self.cache.db_schema),
            ("AGENT", self.agent.reconcile_triage, self.agent.db_schema),
        ):
            if enabled and not on_pg:  # lite-cache's store, lite-agent's session store
                raise ValueError(f"STEWARD_{key} needs PostgreSQL: it is a lite module's PG store")
            if schema == self.db_schema or schema.endswith("_test"):
                # each lite module migrates its own schema; *_test are the ones fixtures drop
                raise ValueError(
                    f"STEWARD_{key}__DB_SCHEMA {schema!r} must be the module's own schema: not "
                    "the governance schema, not a *_test one"
                )
        return self

    @field_validator("db_schema")
    @classmethod
    def _not_a_test_schema(cls, value: str) -> str:
        # Test fixtures DROP ... CASCADE schemas named *_test, so no app process may ever use one.
        if value.endswith("_test"):
            raise ValueError(f"schema names ending in _test are reserved for tests: {value!r}")
        return value
