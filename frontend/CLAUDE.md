@AGENTS.md

# Claude Code notes (frontend)

- Load the `design-direction` skill before choosing the Admin's overall look; the Admin is a dense back-office tool.
- Build pages in the §11 P6 order: Memories → Knowledge Base → Review Queue → Providers → Dashboard → Playground →
  Users → Activity → Jobs → Settings.
- After a backend API change, run `pnpm generate-client` before touching pages that use it.
- Run `pnpm typecheck && pnpm lint && pnpm test` before finishing; run `pnpm e2e` for flow changes, and show a
  screenshot for visual changes.
