@AGENTS.md

# Claude Code notes (backend)

- Before changing `models/` or adding a migration, re-read design §4 and write the migration plan first; run the
  migration on both SQLite and PostgreSQL before saying it works.
- For the state machine, conflict compensation, risk rules and retrieval scoring, write or extend the tests before
  the implementation and run only those while iterating (`uv run pytest tests/unit -k <name>`).
- After changing any route or schema, regenerate the frontend client (`pnpm -C frontend generate-client`) and say so.
- When a test needs an LLM, use `FakeLLM` from `tests/fakes/`; if a behaviour cannot be tested without a real model,
  say so explicitly instead of calling one.
