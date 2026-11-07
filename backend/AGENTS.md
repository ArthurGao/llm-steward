# backend — Agent Guide

FastAPI service that owns all governance logic. Read the root `AGENTS.md` first; this file adds backend rules.
Design: `docs/llm-steward-design.md` §3 (architecture), §4 (data model), §5 (write pipeline), §6 (retrieval),
§7 (API), §8 (extension points), §10 (layout).

## Layout (create in P0/P1)

```
backend/
  pyproject.toml              # uv; optional groups [mem0] [native] [rerank]
  alembic.ini                 # points at src/llm_steward/migrations (shipped inside the package)
  src/llm_steward/
    main.py                   # create_app(): mounts /v1, /admin/v1, /mcp, frontend static files
    migrations/               # Alembic env + versions; `steward migrate` runs them
    config.py                 # pydantic-settings, prefix STEWARD_
    cli.py                    # steward serve | worker | migrate | ns apply | resync | mcp --stdio
    api/deps.py, api/v1/, api/admin/
    mcp/server.py
    schemas/                  # Pydantic request/response models
    models/                   # SQLAlchemy ORM: governance tables (§4)
    services/                 # governance, memory, kb, retrieval, sync, namespace
    providers/                # base.py (Protocols), registry.py (the only name → instance map), weknora.py, …
    pipeline/                 # buffer, conflict, worthiness, risk, maintenance
    llm/                      # LiteLLM wrapper, reranker
    storage/db.py             # engine/session, dialect chosen from the URL
    plugins/, prompts/*.j2, jobs/, embedded.py
  tests/
    conftest.py               # parametrised: sqlite | pg
    fakes/                    # FakeLLM, FakeMemoryProvider, FakeKnowledgeProvider (inject faults and latency)
    contract/  unit/  integration/  e2e/  eval/
```

## Layering (enforced by import-linter in CI)

- `api/`, `mcp/` → `services/` → `providers/`, `pipeline/`, `storage/`. No reverse imports.
- `services/` never imports FastAPI. `providers/` never imports `services/`.
- Providers are called **only** by the Syncer (`sync_service.py`), `RetrievalService` and the health service
  (`health()` probes). API handlers never touch a provider directly.
- MCP tools and REST endpoints share the same services and auth; MCP is not a second implementation.

## Rules

1. **Portable schema.** One set of models runs on PostgreSQL and SQLite: UUIDv7 primary keys, `JSON` type that maps
   to `JSONB` on PG, no PG-only SQL in services. Dialect-specific code lives in `storage/` or `providers/native/`
   (`vector_sqlite.py` vs `vector_pg.py`).
2. **Every migration runs on both dialects.** Test `alembic upgrade head` against SQLite and PG.
3. **Outbox, not inline calls.** A service that changes something a provider must know about enqueues a `jobs` row
   in the same transaction (`sync.upsert`, `sync.remove`, `ingest`, `extract`). Workers claim jobs with
   `locked_until` and retry with backoff; handlers must be idempotent.
4. **Capabilities drive fallbacks.** Read `provider.capabilities`: no `update` → conflict compensation on; no
   `metadata_filter` → over-fetch and filter in the governance layer. Never branch on provider name.
5. **Every LLM call** uses a named Jinja2 template in `prompts/` with a Pydantic output model (structured output).
   No inline prompt strings.
6. **Provider timeouts.** Each provider call in retrieval has its own timeout (default 800 ms); on timeout return the
   other results and mark `degraded`.
7. **Errors** are RFC 9457 Problem Details; write endpoints honour `Idempotency-Key`; lists use cursor pagination.

## Testing

- Unit tests: `FakeLLM` + fake providers only — deterministic, offline. Never a real LLM, never llm-gateway.
- Every DB-touching test is parametrised over SQLite and PostgreSQL; a test that only passes on one is a bug.
- `tests/contract/` runs the same suite against every adapter (write → hit → update → delete → no hit → health).
  `native` is the reference implementation and must always pass. Marked `contract`; real engines come from
  docker compose with pinned versions.
- Governance invariants each need an explicit test, e.g. a high-risk entry never reaches the KB provider before
  approval; a deprecated entry is not returned even when the worker is stopped (safety-net test).

## Commands

```
uv run pytest                     # unit + integration, sqlite and pg
uv run pytest -m contract         # adapters against real engines
uv run ruff check && uv run ruff format --check
uv run mypy
uv run lint-imports
uv run steward migrate            # or: uv run alembic upgrade head
```
