# llm-steward — Agent Guide

llm-steward is a **governance layer for AI memory and knowledge**. It decides what an agent may remember, what it
may trust and what it retrieves: memories pass through screening, end-user confirmation and expert review before
they become trusted, traceable team knowledge. It does **not** re-implement a memory or RAG engine — it drives
mature engines through adapters: mem0 (default memory), WeKnora (default KB), RAGFlow (complex documents) and a
built-in `native` implementation (SQLite + sqlite-vec + FTS5) for tests and single-machine use. Apps and agents
connect through REST, a Python SDK or MCP.

This file is the shared guide for every coding agent. `CLAUDE.md` imports it. `backend/` and `frontend/` each have
their own guide that adds folder-specific rules.

Naming: repo `llm-steward`, Python package `llm_steward`, CLI `steward`, env prefix `STEWARD_`, npm scope
`@llm-steward`, API key format `stw_<prefix>_<secret>`.

## Repository layout

```
backend/     FastAPI service: governance core, provider adapters, pipeline, jobs, MCP server (Python, uv)
frontend/    pnpm workspace: apps/admin (Admin SPA) + packages/widget (@llm-steward/widget, embeddable)
sdk/python/  llm-steward-client (sync + async), generated from OpenAPI then hand-wrapped
deploy/      pinned WeKnora / RAGFlow compose fragments and init scripts
docs/        design — read before writing code
```

`backend/` and `deploy/` exist since P0; `frontend/` gets its pnpm workspace in P6 and `sdk/` in P5.
The only contract between backend and frontend is the backend's
OpenAPI spec (frontend generates its client from it with `@hey-api/openapi-ts`).

## Sources of truth (highest first)

1. `docs/llm-steward-design.md` — the design. §3 architecture, §4 data model, §5 write pipeline, §6 retrieval,
   §7 API, §8 extension points, §10 project structure, §11 stage plan and acceptance.
2. KB at `/Users/arthurgao/Docs/Knowledgebase` (write to it with the `kb-maintain` skill):
   - Engine facts and quirks, per pinned version and shared across projects: `global/mem0/`, `global/weknora/`,
     `global/ragflow/`. Read the engine's page before using, deploying or upgrading it.
   - Project KB `projects/llm-steward/` — decisions and lessons specific to llm-steward. Read it before
     architecture or "which component does X" questions. Empty as of 2026-11-08.

If a change must contradict the design, update the design in the same change and say why in the commit.

## Current stage

**P2 — memory: mem0 behind the governance layer** (P0, P1 closed out). Track the checklist in design §11
(ticked items are done and verified). Next: P3 (KB governance, WeKnora). `docparse` (RAGFlow) is P7, the
frontend workspace P6.

Decided (2026-11-08): default backends mem0 (in-process Python library) + WeKnora v0.9.0; native and mem0
embeddings are local fastembed MiniLM (multilingual, 384-d); API keys arrive in P1 (user tokens / admin JWT
stay in P5); API-key writes are actor `host_app`; real LLM calls in local development go through llm-gateway
subscription profiles — **only when the user has started the gateway and asked for it**; tests use FakeLLM.

Open questions (design §11) — do not decide them silently; ask, or implement the conservative option behind config:
- WeKnora's own embedding model (configured inside WeKnora, P3): local (Ollama) or a remote API?
- License (design suggests Apache-2.0).
- TypeScript SDK in v1?

## Non-negotiable rules

1. **Single write path.** Every write goes through `GovernanceService.apply()`, which writes the object,
   `change_log` and an outbox `jobs` row in **one transaction**. No adapter or plugin may bypass the state machine
   or `change_log`.
2. **Never write to a KB provider from a request handler.** The Syncer (outbox worker) pushes to providers
   asynchronously with exponential backoff.
3. **Only effective knowledge leaves the governance DB.** A KB entry is synced only when `active` or `verified`;
   `deprecated` / `rejected` triggers `sync.remove`. Drafts and pending candidates never reach an external engine.
4. **Retrieval safety net.** Provider hits that do not map back through `external_refs`, or whose object is not
   `active`/`verified` or outside its validity window, are dropped and logged — even if sync is lagging.
5. **End-user confirmation never yields `verified`.** Only a reviewer/admin can verify. MCP tools may only *propose*
   (create candidates); they can never write or approve KB entries.
6. **Invalidate, never delete** governed history: superseding sets `valid_to` / `superseded_by`. The one exception
   is the per-user hard delete (`DELETE /v1/memories?user_id=`) for GDPR-style erasure.
7. **Namespace isolation.** Every business table carries `namespace_id`; every query filters on it.
8. **Pinned engine versions.** `mem0ai==x.y.z`, WeKnora and RAGFlow image tags are exact. Upgrading one requires the
   contract tests (`tests/contract/`) to pass first.
9. **Tests never hit real LLMs or paid APIs.** Unit and integration tests use `FakeLLM` and fake providers.
   Never call llm-gateway subscription profiles from tests. Real engines run only in contract tests.
10. **Secrets** only via environment variables. Never commit `.env*` except `.env.example`. API keys are stored as
    `sha256(secret)` only.

## Tech stack

- Backend: Python 3.12, uv, FastAPI + Pydantic v2, SQLAlchemy 2.1 async + Alembic, OpenAI SDK (role layer over any
  OpenAI-compatible endpoint), mem0 2.2.1 (pinned), official `mcp` SDK
  (streamable HTTP at `/mcp`, plus stdio), structlog, Prometheus `/metrics`.
- Governance DB: PostgreSQL + pgvector (`standard`) or SQLite (`native`). llm-steward and mem0 share one database,
  schemas `steward` and `mem0`. WeKnora runs its own ParadeDB (it needs `pg_search`) and Redis —
  see `deploy/weknora/README.md`.
- Native provider: sqlite-vec (`vec0`) + FTS5 + jieba + fastembed.
- Frontend: React + Vite + TypeScript, TanStack Router/Query, shadcn/ui + Tailwind, Playwright.
- Deployment: Docker Compose with three profiles.

## Local environment

| What | Where |
| --- | --- |
| Governance DB (dev) | shared container `postgresql-postgres-1` (PG 18 + pgvector) on `localhost:5432`, database `llm_steward`, role `llm_steward` |
| Tests | same database via `STEWARD_TEST_DATABASE_URL` (set explicitly, never inherited from the app URL); only schema `steward_test`, dropped and recreated by the fixtures. App data lives in schema `steward`; `Settings` rejects any `*_test` schema name, so no app process can use the one tests drop |
| Secrets | `backend/.env` (uv runs) and root `.env` (docker compose); both gitignored, templates in `.env.example` |
| llm-steward API | `127.0.0.1:8600` |
| WeKnora API / UI (`standard`) | `127.0.0.1:8680` / `127.0.0.1:8681` |

## Commands

Backend (run from `backend/`; `.env` is read from the current directory):

```
uv run pytest                      # every DB test on SQLite and PostgreSQL
uv run ruff check && uv run ruff format --check && uv run mypy && uv run lint-imports
uv run pytest -m model             # opt-in: the real local embedding model (downloads ~220 MB once)
uv run pytest -m contract          # opt-in: adapters against real engines (mem0 on the local PG)
uv run steward migrate             # alembic upgrade head on STEWARD_DATABASE_URL
uv run steward ns create <slug>    # a namespace (no HTTP route creates one)
uv run steward key create <slug>   # prints an API key once: Authorization: Bearer stw_<prefix>_<secret>
uv run steward serve               # http://127.0.0.1:8600 (+ outbox worker unless STEWARD_RUN_WORKER=false)
uv run steward worker              # the outbox worker on its own
```

Stack (run from the repo root):

```
docker compose --profile native up -d --build      # llm-steward alone, SQLite volume
docker compose --profile standard up -d --build    # llm-steward + WeKnora, shared PG
```

Later stages add `uv run pytest -m contract` for real engines (P2+), `pnpm -C frontend test` and
`generate-client` (P6).

## Workflow

1. Work stage by stage (§11, P0 → P7). One stage = one PR with its acceptance checks automated.
2. Governance state machine, conflict compensation, risk rules and retrieval scoring: write tests first.
3. Definition of done: ruff, mypy, import-linter and tests pass on **both** SQLite and PostgreSQL; frontend
   typecheck/lint/test pass; acceptance items covered by tests; design doc updated if behaviour changed.
4. Conventional Commits (`feat(governance): …`, `fix(retrieval): …`). All code-facing text in English.
5. **Stage close-out — after a stage's implementation passes its checks, and before the next stage starts:**
   1. Simplify the stage's full diff (reuse, simplification, efficiency, altitude) and apply the fixes.
   2. Independent code review by Codex of the same diff (`codex review --base <commit before the stage>`, or
      `--uncommitted` before the first commit).
   3. Fix every finding that holds up — a bug gets a failing regression test first. A finding you reject is
      written down with the reason; never silently dropped.
   4. Re-run all checks (step 3) so they cover the final code, then report the review outcome with the stage.
   The next stage does not start until this loop is finished.

## Glossary

| Term | Meaning |
| --- | --- |
| Namespace | Unit of isolation and config, usually one integrating project; own API keys, config and providers |
| Scope | Ownership inside a namespace: `user_id` / `agent_id` / `session_id`; NULL = shared |
| Provider | Engine adapter: `MemoryProvider` (mem0 / native), `KnowledgeProvider` (WeKnora / RAGFlow / native) |
| Memory | One atomic fact; content lives in the memory provider, llm-steward keeps a mirror + governance metadata |
| KB Entry | One governed atomic piece of knowledge; **llm-steward is its source of truth** |
| KB Document | An uploaded document; parsed and chunked by the KB provider |
| Candidate | A pending write proposal (extraction, promote, user suggestion); `awaiting_user` or `awaiting_review` |
| Episode | Raw input (conversation, import, manual action); provenance for everything derived from it |
| External Ref | Mapping from a llm-steward object to a provider id, with sync status and synced version |
| Promote | Upgrading a memory into a KB candidate, rewritten as a general statement |
| Retrieval | One joint query across providers: RRF fusion, trust/importance/decay weighting, assembled context |
