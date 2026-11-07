@AGENTS.md

# Claude Code notes

- At the start of a session: check §11 of `docs/llm-steward-design.md` for the current stage and its acceptance
  list, and skim the project KB at `/Users/arthurgao/Docs/Knowledgebase/projects/llm-steward/`.
- Working inside `backend/` or `frontend/` loads that folder's `CLAUDE.md` as well — follow both.
- The design doc is 800+ lines: read it by section (`rg -n '^#' docs/llm-steward-design.md` for the map), not whole.
- Use plan mode for anything that changes the governance tables, the state machine, the provider Protocols or the
  retrieval scoring, or touches more than one layer.
- Relevant user-level skills: `rag-design` (retrieval, chunking, hybrid search), `llm-app-architecture` (LiteLLM
  call sites, model choice), `postgres-rls-migrations` (Alembic, multi-tenancy), `backend-reliability` (outbox,
  worker, retries), `testing-and-ci`, `web-authz` (API keys, user tokens, MCP auth), `kb-maintain` (writing the KB).
- Good uses of subagents: (1) check a new adapter against the contract tests and the Protocol in design §8;
  (2) security review of new endpoints and MCP tools (namespace isolation, user-token reach, MCP cannot write KB);
  (3) verify governance invariants (rules 1–6 in `AGENTS.md`) against a diff.
- Never answer a mem0 / WeKnora / RAGFlow API question from memory: check context7 or the pinned version's docs,
  and record surprises in the KB.
