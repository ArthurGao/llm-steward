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

Planned in design §10; created during P0. The only contract between backend and frontend is the backend's
OpenAPI spec (frontend generates its client from it with `@hey-api/openapi-ts`).

## Sources of truth (highest first)

1. `docs/llm-steward-design.md` — the design. §3 architecture, §4 data model, §5 write pipeline, §6 retrieval,
   §7 API, §8 extension points, §10 project structure, §11 stage plan and acceptance. The doc refers to itself as
   `docs/design.md`; the real path is `docs/llm-steward-design.md`.
2. Project KB: `/Users/arthurgao/Docs/Knowledgebase/projects/llm-steward/` — decisions, findings about the engines
   (mem0 / WeKnora / RAGFlow quirks), lessons. Read it before architecture or "which component does X" questions.
   Empty as of 2026-11-08; write to it with the `kb-maintain` skill.

If a change must contradict the design, update the design in the same change and say why in the commit.

## Current stage

**P0 — project skeleton (not started).** Scope: monorepo layout from §10, uv / ruff / mypy / import-linter / pytest,
`config.py`, `storage/db.py` switching SQLite ↔ PostgreSQL on `STEWARD_DATABASE_URL`, Alembic, docker-compose
profiles `native` / `standard` / `docparse` (pinned WeKnora and RAGFlow images), CI matrix.
Acceptance: `native` and `standard` both start, `GET /health` reports each component, CI green. **Do not start P1
until P0 acceptance is reported item by item.**

Open questions (design §11) — do not decide them silently; ask, or implement the conservative option behind config:
- Default LLM / embedding: network calls allowed, or local by default (fastembed / Ollama)?
- mem0 in-process (design default) or the self-hosted mem0 REST server?
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

- Backend: Python 3.12, uv, FastAPI + Pydantic v2, SQLAlchemy 2.0 async + Alembic, LiteLLM, official `mcp` SDK
  (streamable HTTP at `/mcp`, plus stdio), structlog, Prometheus `/metrics`.
- Governance DB: PostgreSQL 16 + pgvector (`standard`, `docparse`) or SQLite (`native`). One PG instance shared with
  mem0 and WeKnora, separate schemas (`steward`, `mem0`, `weknora`).
- Native provider: sqlite-vec (`vec0`) + FTS5 + jieba + fastembed.
- Frontend: React + Vite + TypeScript, TanStack Router/Query, shadcn/ui + Tailwind, Playwright.
- Deployment: Docker Compose with three profiles.

## Commands (available after P0)

```
cd backend  && uv run pytest && uv run ruff check && uv run mypy src && uv run lint-imports
cd backend  && uv run pytest -m contract        # needs real engines via docker compose
pnpm -C frontend test
pnpm -C frontend generate-client                 # after any backend API change
docker compose --profile native up               # or: standard | docparse
```

## Workflow

1. Work stage by stage (§11, P0 → P7). One stage = one PR with its acceptance checks automated.
2. Governance state machine, conflict compensation, risk rules and retrieval scoring: write tests first.
3. Definition of done: ruff, mypy, import-linter and tests pass on **both** SQLite and PostgreSQL; frontend
   typecheck/lint/test pass; acceptance items covered by tests; design doc updated if behaviour changed.
4. Conventional Commits (`feat(governance): …`, `fix(retrieval): …`). All code-facing text in English.

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
