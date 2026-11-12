# llm-steward

A governance layer for AI memory and knowledge. It decides what an agent may remember, what it may
trust and what it retrieves.

Agents and apps write through llm-steward. Every memory and every piece of team knowledge passes
screening (secrets and personal data are redacted, risk is labelled), end-user confirmation and
expert review before it becomes trusted. Every change is versioned, traceable to the conversation
it came from and reversible. Retrieval joins personal memory and team knowledge in one ranked,
token-budgeted context, and drops anything not currently effective, even while an engine lags.

llm-steward does not re-implement a memory or RAG engine. It drives mature ones through adapters:

| Engine | Role | Profile |
| --- | --- | --- |
| [lite-mem](https://github.com/ArthurGao/lite-mem) (the owner's engine, pinned to a commit) | memory (default): extracts facts and resolves conflicts itself, in-process | `standard`, `docparse` |
| [mem0](https://github.com/mem0ai/mem0) 2.2.1 | memory, extracting facts itself; llm-steward resolves conflicts (`STEWARD_MEMORY_PROVIDER=mem0`) | `standard`, `docparse` |
| [WeKnora](https://github.com/Tencent/WeKnora) v0.9.0 | knowledge base, documents | `standard` |
| [RAGFlow](https://github.com/infiniflow/ragflow) v1.0.0 | knowledge base, complex documents | `docparse` |
| built-in `native` (SQLite or PostgreSQL, sqlite-vec / pgvector, local embeddings) | both; for memory, llm-steward extracts the facts (`STEWARD_MEMORY_LLM=true`) | `native`, or the memory engine of any profile |

Apps connect over REST, the [Python SDK](sdk/python/README.md) or MCP; end users answer
confirmation cards in the [widget](frontend/packages/widget/README.md); reviewers work in the Admin.

```
 app / agent ──REST · SDK · MCP──▶ llm-steward ──outbox worker──▶ lite-mem · mem0 · WeKnora · RAGFlow · native
 end user ─────widget──────────▶  (screening, review,          ◀──joint retrieval──
 reviewer ─────Admin───────────▶   versions, audit log)
```

## Try it in two minutes (`native`)

Everything in one container: SQLite, native engines, no LLM. Needs Docker.

```sh
git clone https://github.com/ArthurGao/llm-steward && cd llm-steward
cp .env.example .env          # compose reads it; native needs none of its values
docker compose --profile native up -d --build --wait
docker compose exec steward-native steward ns create demo
docker compose exec steward-native steward key create demo                                 # your app
docker compose exec steward-native steward key create demo --scope write --scope review    # a reviewer
export KEY=stw_...  REVIEW_KEY=stw_...                          # each key is printed once
```

Then the [first write and retrieve](#first-write-and-retrieve) below. The first call downloads the
local embedding model (about 240 MB) into the container's volume.

## Deploy `standard` (lite-mem + WeKnora)

About 15 minutes on a fresh machine, most of it image downloads (about 5 minutes once the images
are pulled).

**You need:** Docker with Compose v2.20 or later; [Ollama](https://ollama.com) on the host with the
embedding model WeKnora and lite-mem use (`ollama pull bge-m3:567m`); Python 3 on the host (standard
library only, for the bootstrap script); an LLM for memory extraction: an OpenAI-compatible endpoint
(base URL, key, model), or leave those unset and `ollama pull qwen3:8b` for lite-mem on the host.

1. **Configure.** Copy the template and fill in the secrets:

   ```sh
   cp .env.example .env
   ```

   | Variable | Value |
   | --- | --- |
   | `STEWARD_DB_PASSWORD`, `WEKNORA_DB_PASSWORD`, `WEKNORA_REDIS_PASSWORD`, `WEKNORA_SYSTEM_AES_KEY` | `openssl rand -hex 16` each (keep the AES key: losing it makes WeKnora's encrypted fields unreadable) |
   | `WEKNORA_JWT_SECRET` | `openssl rand -hex 32` |
   | `WEKNORA_ADMIN_PASSWORD` | 8–32 letters and digits (WeKnora's owner account) |
   | `STEWARD_LLM_BASE_URL`, `STEWARD_LLM_API_KEY`, `STEWARD_LLM_MODEL` | your LLM endpoint, e.g. `https://api.openai.com/v1`, a key, `gpt-4.1-mini` (unset: lite-mem uses `qwen3:8b` on the host's Ollama; mem0 needs them) |
   | `STEWARD_MEMORY_PROVIDER`, `STEWARD_MEMORY_LLM` | the memory engine: `litemem` / `false` (default: [lite-mem](https://github.com/ArthurGao/lite-mem) in-process, which extracts and resolves conflicts itself), `mem0` / `false` (mem0 2.2.1; llm-steward compensates conflicts), or `native` / `true` (llm-steward extracts the facts). Switching an existing deployment: `steward resync` afterwards |

   `--profile db` below runs a bundled PostgreSQL + pgvector for llm-steward and its memory engine
   (lite-mem in schema `lite_mem`, or mem0 in `mem0`). To use your own instead, set
   `STEWARD_STANDARD_DATABASE_URL` (as seen from inside a container) and leave out `--profile db`.

2. **Start the stack.**

   ```sh
   docker compose --profile standard --profile db up -d --build --wait
   ```

   llm-steward migrates its database (and lite-mem's schema) on start. WeKnora's first start takes
   a minute or two. lite-mem's first call loads `bge-m3:567m` into Ollama; until Ollama answers,
   `/health` reports the memory provider as an error.

3. **Bootstrap WeKnora.** It has no default account, API key or model. This registers the owner,
   registers `bge-m3:567m` from your Ollama, waits until it is active and creates an API key:

   ```sh
   (set -a; . ./.env; python3 deploy/weknora/bootstrap.py)
   ```

   (The subshell matters: variables exported into your shell override `.env` for compose, so the
   still-empty keys would win over the ones you are about to paste.)

   Copy the `STEWARD_WEKNORA_API_KEY` and `STEWARD_WEKNORA_EMBEDDING_MODEL_ID` it prints into
   `.env`, then recreate llm-steward so it picks them up:

   ```sh
   docker compose --profile standard --profile db up -d --wait
   curl -s http://127.0.0.1:8600/health    # every check "ok"
   ```

4. **Create a namespace and keys.** A namespace is one integrating app: its own keys, config and
   data. No HTTP route creates one.

   ```sh
   docker compose exec steward-standard steward ns create demo
   docker compose exec steward-standard steward key create demo                  # read + write: your app
   docker compose exec steward-standard steward key create demo --scope write --scope review   # a reviewer
   export KEY=stw_...  REVIEW_KEY=stw_...
   ```

   Keys are printed once and stored only as a hash.

Ports, all on 127.0.0.1: llm-steward 8600, WeKnora API 8680, WeKnora UI 8681. More on WeKnora in
[deploy/weknora/README.md](deploy/weknora/README.md); RAGFlow (`docparse`) in
[deploy/ragflow/README.md](deploy/ragflow/README.md).

## First write and retrieve

A memory for one end user. `"infer": false` stores the fact as given; leave it out and the memory
engine extracts facts itself (that calls your LLM):

```sh
curl -s http://127.0.0.1:8600/v1/memories -H "Authorization: Bearer $KEY" \
  -H 'Content-Type: application/json' \
  -d '{"content": "Ada prefers refunds to store credit", "user_id": "ada", "infer": false}'
```

Team knowledge goes into a collection. Written with a reviewer's key it is verified at once; from
your app's key it is routed by risk (see [below](#how-knowledge-becomes-trusted)):

```sh
COL=$(curl -s http://127.0.0.1:8600/v1/kb/collections -H "Authorization: Bearer $REVIEW_KEY" \
  -H 'Content-Type: application/json' -d '{"name": "support"}' | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
curl -s http://127.0.0.1:8600/v1/kb/entries -H "Authorization: Bearer $REVIEW_KEY" \
  -H 'Content-Type: application/json' \
  -d "{\"collection_id\": \"$COL\", \"title\": \"Refund time\", \"content\": \"Refunds reach the card within 5 business days.\"}"
```

The worker pushes both to the engines within seconds. Then one query across memory and knowledge,
as a context block ready for the prompt:

```sh
curl -s http://127.0.0.1:8600/v1/retrieve -H "Authorization: Bearer $KEY" \
  -H 'Content-Type: application/json' \
  -d '{"query": "how long does a refund take?", "scope": {"user_id": "ada"}, "format": "context"}'
```

The full API is at http://127.0.0.1:8600/docs.

## Connect an app or an agent

- **Conversations:** `POST /v1/episodes` with the messages; the memory engine extracts what is worth
  keeping, and anything that could become team knowledge comes back as a suggestion for the user.
- **Python:** `pip install "llm-steward-client @ git+https://github.com/ArthurGao/llm-steward#subdirectory=sdk/python"`
  (not on PyPI yet), then `Steward(base_url=..., api_key=...)`; it sends
  idempotency keys and retries safely. It also runs embedded on SQLite. See
  [sdk/python/README.md](sdk/python/README.md).
- **MCP** (Claude Code, Cursor, any MCP client): six tools (`remember`, `recall`, `forget`,
  `suggest_kb`, `search_kb`, `get_profile`). MCP can only propose knowledge, never write or approve it.

  ```sh
  claude mcp add --transport http steward http://127.0.0.1:8600/mcp --header "Authorization: Bearer $KEY"
  # or local, over stdio, from backend/:
  STEWARD_API_KEY=$KEY uv run steward mcp --stdio --user-id ada
  ```

- **Browsers:** never ship an API key. Your server mints a short-lived user token
  (`POST /v1/tokens/user`, needs `STEWARD_TOKEN_SECRET`), which reaches only that user's own data.
  The [`@llm-steward/widget`](frontend/packages/widget/README.md) React components use it for
  confirmation cards and a "what we remember about you" panel.
- **Events:** webhooks per namespace (`candidate.created`, `kb_entry.verified`, …), signed with
  HMAC-SHA256; configured with `steward ns apply -f ns.yaml`.

## How knowledge becomes trusted

1. **Screen.** Secrets and personal data are redacted; rules (and, if enabled, an LLM) label risk.
2. **Route by risk.** A reviewer's own knowledge is verified at once. From an app, the collection's
   review policy decides; by default low risk goes live as `active`, medium risk asks the end user
   on a confirmation card, high risk waits in the review queue. Text a model drafted always gets a
   person's look first. An end user's yes never makes anything `verified`: only a reviewer verifies.
   A reviewer who rejects says why (`note`, required on every route); an end user can just say no.
   Through a user token, an edited card is kept only for that user; your server (an API key) may
   pass the user's edit on to the team, and the edit is screened again.
3. **Sync.** Only `active` and `verified` knowledge leaves the governance database, pushed by an
   outbox worker with retries; retired knowledge is removed from the engine.
4. **Retrieve.** Results from every engine are fused, weighted by trust, importance and age, and
   any hit that is not currently effective is dropped, even if an engine still has it.
5. **Maintain.** Expired cards lapse, knowledge rated unhelpful is flagged for review, retrieval
   misses show up as gaps in the Admin.

End users stay in charge of what they are asked: "stop suggesting things to me" on a card becomes
a rule they can see among their memories and delete to undo. It is llm-steward's own state (the
reserved `steward.` key space, which no client can write), so it is never recalled as a fact
about them and never sent to the memory engine.

Nothing governed is deleted: a new version or a retirement keeps the history. The exception is
erasing one user's memories (`DELETE /v1/memories?user_id=`), for GDPR-style requests.

## Configure a namespace

Configuration is code: one YAML file per namespace, validated before it is stored, replacing the
whole config (`steward ns show demo` prints the current one).

```yaml
slug: demo
name: Demo support
kb:
  suggest_threshold: 0.7            # how sure the model must be before a card is suggested
  high_risk_keywords: [refund policy, legal, medical]   # always the review queue
retrieval:
  top_k: 8
  token_budget: 2000                # estimated tokens of the returned items
  rerank: false                     # a local cross-encoder (STEWARD_RERANK_MODEL) re-scores the best 30
  min_relevance: null               # with rerank only: drop what it rates below this (0-1); none left = a gap
  context_language: auto            # format=context headings: the query's language, or en / zh
memory:
  types: [{name: episodic, half_life_days: 30}]
limits:
  requests_per_minute: 600          # per API key, in this process
webhooks:
  - url: https://app.example.com/hooks/steward
    events: [candidate.created, kb_entry.verified]
    secret_env: STEWARD_WEBHOOK_SECRET   # the secret itself stays in the environment
```

```sh
docker compose cp demo.yaml steward-standard:/tmp/demo.yaml
docker compose exec steward-standard steward ns apply -f /tmp/demo.yaml
```

## Operations

```sh
docker compose exec steward-standard steward jobs failed           # pushes out of retries
docker compose exec steward-standard steward jobs retry --all-failed
docker compose exec steward-standard steward resync                # after switching engines
```

Switching a namespace's KB engine (say WeKnora → RAGFlow) is a config change plus `steward resync`;
llm-steward is the source of truth for knowledge and pushes what the new engine lacks. The old
engine keeps its copies and is no longer reachable: removals still queued for it fail (visible in
`steward jobs failed`), and resync names retired objects it still holds. Clear or drop it.

The Admin (reviews, memories, knowledge, gaps, jobs) is a separate SPA in `frontend/apps/admin`. It
has no accounts: it connects to a backend with the static `STEWARD_ADMIN_TOKEN`.

## Development

```sh
cd backend && uv sync --all-extras
uv run pytest                      # every DB test on SQLite and PostgreSQL (needs STEWARD_TEST_DATABASE_URL)
uv run ruff check && uv run mypy && uv run lint-imports
uv run steward serve               # http://127.0.0.1:8600, with the outbox worker

cd frontend && pnpm install
pnpm dev                           # the Admin on http://127.0.0.1:8610, with demo data
```

Tests never call a real LLM or paid API. The design is in
[docs/llm-steward-design.md](docs/llm-steward-design.md); contributor rules in [AGENTS.md](AGENTS.md).

## License

[Apache-2.0](LICENSE)
