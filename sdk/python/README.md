# llm-steward-client

Python client for [llm-steward](../../README.md), the governance layer for AI memory and knowledge:
sync (`Steward`) and async (`AsyncSteward`), and an embedded mode that runs llm-steward in your
process on SQLite. Responses are the API's own models (`llm_steward_client.models`), generated from
its OpenAPI spec.

```python
from llm_steward_client import Steward

st = Steward(base_url="http://127.0.0.1:8600", api_key="stw_...")

# a conversation: the memory engine extracts what is worth keeping
res = st.episodes.add(messages=msgs, user_id="u_123", session_id="s_9")

# joint retrieval, ready for the prompt
ctx = st.retrieve("my last refund", user_id="u_123", format="context")
system_prompt = BASE_PROMPT + (ctx.context or "")

# a confirmation card the user answered
for card in res.suggestions:
    st.candidates.accept(card.id, user_id="u_123", scope="team")
```

## What the client does for you

- **Writes are idempotent.** Every POST, PATCH and DELETE carries an `Idempotency-Key`, the same one
  across its retries, so a retried write is applied once.
- **Retries** 408, 429 (honouring `Retry-After`, at most 30 s), 5xx and network errors, `max_retries`
  times (default 3). **Never** 401 or 403: a credential problem is raised at once.
- **Errors** are typed: `AuthenticationError`, `PermissionDeniedError`, `NotFoundError`,
  `ConflictError`, `UnprocessableError`, `RateLimitError`, `ServerError`, `StewardConnectionError`,
  all `StewardError` with the server's `status`, `code` and `detail`.
- **Browsers** get a user token from your server (`st.tokens.user("u_123").token`) and use it as the
  `api_key`; it reaches only that user's own memories, cards and retrievals.

## Embedded mode

```python
# pip install 'llm-steward-client[embedded]'
st = Steward.embedded("sqlite+aiosqlite:///./steward.db")
st.memories.create("Ada prefers green tea", user_id="ada")
st.close()
```

The database is migrated on start, a `default` namespace is created if missing, and the outbox worker
runs in-process. There is no API key: the process that owns the database is its only caller, with
`read` and `write` (pass `review=True` for reviewer rights). Native engines (SQLite + sqlite-vec,
local embeddings) unless `settings={...}` say otherwise.

## Development

```
uv sync --all-extras
uv run pytest                       # includes the drift check below
uv run python scripts/generate.py   # after an API change: re-export openapi.json, regenerate models
```
