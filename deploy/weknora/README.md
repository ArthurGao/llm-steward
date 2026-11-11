# WeKnora (default KB provider) — pinned deployment

`compose.yml` here is included by the root `docker-compose.yml` under the `standard` profile.

| Item | Value |
| --- | --- |
| Version | **v0.9.0** (released 2026-10-29), images `wechatopenai/weknora-{app,ui,docreader}:v0.9.0` |
| Database | `paradedb/paradedb:v0.25.11-pg17` — its own instance, not the shared local PG |
| Ports | app API `127.0.0.1:8680`, web UI `127.0.0.1:8681`; Postgres, Redis and docreader stay internal |
| Config | `config/config.yaml`, copied verbatim from upstream `v0.9.0` |
| Secrets | root `.env`: `WEKNORA_DB_PASSWORD`, `WEKNORA_REDIS_PASSWORD`, `WEKNORA_JWT_SECRET`, `WEKNORA_SYSTEM_AES_KEY` |

## First start (bootstrap)

WeKnora has no default account, API key or models. After `docker compose --profile standard up -d`
(and with Ollama running on the host, `bge-m3:567m` pulled):

```
# a subshell: exported variables would override .env for later compose runs
(set -a; . ./.env; python3 deploy/weknora/bootstrap.py)
```

It registers the owner (WEKNORA_ADMIN_*), registers the embedding model `bge-m3:567m` (type
`Embedding`, source `local`, 1024 dimensions — WeKnora pulls it from the host's Ollama through
`OLLAMA_BASE_URL`) and waits until it is `active`, then creates a full-access workspace API key. It
prints `STEWARD_WEKNORA_URL`, `STEWARD_WEKNORA_API_KEY` and `STEWARD_WEKNORA_EMBEDDING_MODEL_ID`:
the last two go into the root `.env` (compose) or `backend/.env` (uv runs; there the URL too).
Running it again finds the owner and the model, and creates one more key (delete the old one in the
UI or `DELETE /api/v1/tenants/{id}/api-keys/{key_id}`).

## Why WeKnora has its own Postgres

WeKnora's migration `000002_embeddings` runs `CREATE EXTENSION pg_search` and builds a `USING bm25`
index (ParadeDB). The shared local PG (PostgreSQL 18 + pgvector) has no `pg_search`, so WeKnora cannot
use it. llm-steward and mem0 only need pgvector and use the shared PG.

## Trimmed from upstream

Kept the minimum stack: `app`, `frontend` (as `weknora-ui`), `docreader`, `postgres`, `redis`. Dropped the
optional services (sandbox, searxng, minio, neo4j, qdrant, milvus, weaviate, doris, dex, langfuse, mcp)
and upstream's long `environment` list: anything not set here falls back to WeKnora's built-in defaults.
Services are renamed `weknora-*` and have no `container_name`, so they cannot collide with other stacks.

## Upgrading

1. Read the upstream release notes and diff `docker-compose.yml`, `.env.example` and `config/config.yaml`
   between the old and new tag.
2. Change every image tag here in one commit (app, ui and docreader move together).
3. If the ParadeDB image changes, upstream requires the `pg_search` extension catalog to be upgraded to
   match (see upstream `website-docs/01-getting-started/06-paradedb-upgrade.md`).
4. Run the WeKnora contract tests (`uv run pytest -m contract`, from P3 on) before merging.
