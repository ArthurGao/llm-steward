# RAGFlow (KB provider for complex documents) — pinned deployment

RAGFlow is the `docparse` profile's KB engine: deep document parsing (layout, tables, scans). It runs
as **its own compose project**, used as upstream ships it. `setup.py` fetches upstream's `docker/`
directory at the pinned tag into `upstream/` (gitignored), then changes only its `.env`. Facts and
gotchas: KB `global/ragflow/README.md`.

| Item | Value |
| --- | --- |
| Version | **v1.0.0** (released 2026-11-06), image `infiniflow/ragflow:v1.0.0` (amd64 and arm64) |
| Stack | Elasticsearch 8.11.3, MySQL 8.0.40, MinIO-compatible `pgsty/silo`, Kvrocks 2.16.0, NATS 2.14.2; about 5.5 GB of memory, 3.7 GB of images |
| Ports | all on 127.0.0.1: API `8690`, web UI `8691`, admin `8692`, HTTPS `8693`; ES `1200`, MySQL `13306`, MinIO `19000`/`19001`, Kvrocks `16379`, NATS `14222`/`18222` |
| Secrets | `upstream/.env` (mode 600): the five sample passwords replaced with random ones on the first run and kept after; root `.env`: `RAGFLOW_ADMIN_*` (the account llm-steward uses) |
| Embeddings | the host's Ollama (`bge-m3:567m`), reached as `host.docker.internal:11434` (so `ALLOW_ANY_HOST=1`, development only) |

## First start

With Ollama running on the host and `bge-m3:567m` pulled:

```
python deploy/ragflow/setup.py
docker compose -p llm-steward-ragflow -f deploy/ragflow/upstream/docker-compose.yml up -d
curl -s http://127.0.0.1:8690/api/v1/system/healthz        # {"…","status":"ok"} within a minute
# a subshell: exported variables (RAGFLOW_ADMIN_*, the still-empty key) would override .env for compose
(set -a; . ./.env; uv run --project backend python deploy/ragflow/bootstrap.py >> backend/.env)  # gitignored
```

`bootstrap.py` registers the account and logs in (the password travels RSA-encrypted with RAGFlow's
public key). It adds the Ollama provider with an instance serving `bge-m3:567m`, makes that the tenant's
default embedding model (new datasets use it), and mints an API key. It prints `STEWARD_RAGFLOW_URL`
and `STEWARD_RAGFLOW_API_KEY`. Running it again finds the account and the instance, and mints one more
key (old ones: RAGFlow's UI, API page).

For the `docparse` container, put `STEWARD_RAGFLOW_API_KEY` in the root `.env` as well. It reaches
RAGFlow at `host.docker.internal:8690`.

Then `uv run pytest -m contract -k ragflow` (from `backend/`) runs the contract and the governance
acceptance against it.

## Changed from upstream

Only `upstream/.env`, by `setup.py`:
- every published port bound to 127.0.0.1 and moved off ports this machine uses (upstream publishes
  MySQL, MinIO, ES … on all interfaces with sample passwords);
- the sample passwords replaced;
- `ALLOW_ANY_HOST=1` for the host's Ollama.

Rerunning `setup.py` fetches upstream again and keeps `upstream/.env`, so the stack's data stays usable.

## Upgrading

1. Read the release notes and diff upstream's `docker/` between the two tags. Watch for: new services or
   ports in `docker-compose-base.yml`, renamed `.env` variables (`setup.py` stops if a variable it sets
   is missing), API changes (the 1.0 migration notes in the HTTP API reference).
2. Change `TAG` in `setup.py` (the image check follows it), run it, restart the stack.
3. Run the RAGFlow contract tests before merging, then record what changed in the KB page.
