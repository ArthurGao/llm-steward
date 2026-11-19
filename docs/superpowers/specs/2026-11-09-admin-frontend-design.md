# Admin frontend — mock-data demo on Vercel

Date: 2026-11-09. Status: builder-checked; awaiting owner approval.

## Intent

Ship the llm-steward Admin SPA (design §9) early, before the backend has `/admin/v1` or Admin JWT (P5), as a
**mock-data demo** deployed to the owner's personal Vercel. The first audience is open-source visitors who
arrive from the GitHub README: within a minute they should understand that llm-steward turns AI memories into
trusted, versioned, traceable knowledge — and that nothing becomes trusted without review, and nothing
governed is ever deleted.

The same app later becomes the real Admin: switching off the mock layer and pointing it at the backend must
need no page changes. It is deployed on its own (a separate Vercel project), independent of the backend and of
the future `@llm-steward/widget` package.

## Goals

1. pnpm workspace in `frontend/` with `apps/admin` (React + Vite + TypeScript, TanStack Router + Query,
   shadcn/ui primitives + Tailwind) — the stack fixed in `AGENTS.md`.
2. **All ten §9 pages plus a login page, with mock data and working interactions**: Dashboard, Memories,
   Users, Knowledge Base, Review Queue, Retrieval Playground, Activity Log, Jobs, Settings, Providers.
3. Shell: top bar with text tabs, namespace switcher, user menu; list + right-hand detail drawer pattern;
   filters, search and pagination on every list (design §9).
4. Visual direction "Standards Track" (below), recorded in `.impeccable/surfaces/frontend-apps-admin.md`.
5. Deployed to Vercel (owner's Hobby team, CLI user `arthurgaonz-2934`) as project `llm-steward-admin`.
6. Checks: typecheck, lint, unit tests (Vitest), Playwright e2e, production build — locally and in CI.

## Non-goals

- No real backend calls, no real auth, no `/admin/v1` routes in the backend (P5 does those).
- No `@llm-steward/widget` (P7). No TypeScript SDK (open question in design §11 stays open).
- No i18n: UI copy is English.
- No persistence of demo edits beyond the browser session.

## Anti-goals (a failure even if it works)

- Looks like every AI dev tool: dark ground, one glowing accent, card grids (the reason for the direction below).
- Mock data that could be mistaken for real usage: invented customers, logos, benchmarks or metrics presented
  as real. Every page carries a visible "Demo data" marker; the namespace is an obviously fictional
  `acme-support`.
- A mock layer that leaks into page code (pages importing fixtures directly), so going live means rewriting pages.
- Demo flows that contradict governance rules (`AGENTS.md` 1–7): e.g. an end user "verifying" an entry, a
  hard delete of a governed KB entry, a draft shown as synced to a provider.

## Constraints

- Stack and naming from `AGENTS.md` (`@llm-steward` npm scope; frontend contract with backend is OpenAPI
  later, via `@hey-api/openapi-ts`).
- Node 22 (nvm), pnpm 10 on this machine. Dev server port **8610** (free, 2026-11-09; register it in the KB
  ports registry).
- Vercel Hobby = non-commercial only; fine for an open-source demo.
- Code-facing text in English; the global hook enforces it for JS/TS.

## Decisions the owner made

- Mock data first, as a showcase (2026-11-09).
- All pages present, all with mock data (2026-11-09).
- Deploy to the owner's own Vercel (2026-11-09).
- Visual direction **Standards Track** (picked on the Impeccable decision page, 2026-11-09).
- Audience: open-source visitors / developers. No brand assets exist; design decides.
- **Interaction depth:** core governance actions work against the mock store — approve / edit & approve /
  reject (reason required), deprecate, edit an entry, promote a memory, pin, delete a memory, user erasure,
  job retry / cancel, retry a failed sync, create and revoke an API key (secret shown once), Playground
  query and "add missing answer to KB". Secondary actions are present with data but read-only, their buttons
  marked "Not available in the demo": Settings YAML save, prompt-template test run, member management,
  embedding-space switch; KB document upload, chunk view refresh, reprocess; Providers reconcile and
  provider switch with full resync.
- **Delivery:** its own branch `feat/admin-frontend` in a git worktree (main holds another session's
  uncommitted P3 work, which this must not touch), with the full stage close-out from `AGENTS.md`
  (`/simplify`, then Codex review, fixes, re-run checks) before reporting.
- **Pre-authorised after verification:** `vercel deploy --prod` (creates `llm-steward-admin`), adding the
  CI `frontend` job, registering port 8610 in the KB. Commits still need the owner's go-ahead each time.

## Visual direction — Standards Track

Every governed object reads like an IETF standards-track document. Its detail view opens with a mono header
block: Status, Risk, Version, Obsoletes / Obsoleted-by, Source, timestamps. Supersession is the navigation:
the Obsoletes / Obsoleted-by links walk the version chain in place, each version opening as a stacked document
(the signature interaction). White paper ground, ink text, hairline rules, square corners, no decorative
shadow. Status colours: awaiting (amber), active (ink), verified (green), deprecated (grey), rejected (red);
links blue. Red Hat Text for UI, Red Hat Mono only for header blocks, ids, numbers and diffs. The full
direction contract lives in the surface brief; `PRODUCT.md` holds product truth. `DESIGN.md` is written at the
end from the shipped build (Impeccable documenter), not before.

## How it works (decided as the builder's calls — owner may override)

- **Contract source.** Where the backend already has schemas (`backend/src/llm_steward/schemas/` —
  memories, kb, candidates, episodes, retrieve, including uncommitted P3 files), the mock types follow them;
  design §4 / §7 fills only the gaps (stats, jobs, providers, logs, settings, keys). Behaviour follows the
  backend too: editing verified knowledge through the API yields a candidate (`Written {entry | candidate}`),
  `Decision.scope` (personal / team) exists, memory statuses are the backend's, not `PRODUCT.md`'s summary.
- **Two kinds of history (builder's call, from the backend).** Memories version by row: a new fact
  supersedes the old via `superseded_by` (the backend's `chain`). KB entries version in place (`version += 1`,
  earlier text in `change_log.before`), and one entry obsoletes another via `superseded_by`. The signature
  interaction walks both: a memory's chain, a KB entry's versions (mock `GET .../history` built from the
  change log) and its Obsoletes / Obsoleted-by links.
- **User erasure (builder's call, conservative, mock only).** `DELETE /memories?user_id=` physically removes
  that user's memories and episodes; the change log keeps the events but redacts their content, and one
  `erasure` event records who erased whose data and how many objects. The backend decides for real in P5;
  record the choice there.
- **API seam.** Pages call typed functions in `src/api/` that `fetch` `/admin/v1/...` paths shaped after
  design §7 and §4. Types are hand-written in `src/api/types.ts` for now; in P5 they are replaced by the
  generated client, and the paths/types here become the first draft of the `/admin/v1` contract.
- **Mock layer = MSW** (Mock Service Worker) running in the browser, enabled when `VITE_API_MODE=mock`
  (default, and what Vercel builds). Handlers serve an in-memory store seeded with deterministic fixtures
  (fixed seed, fixed "now"), so screenshots and e2e tests are stable. Mutations (approve, reject, edit,
  pin, deprecate, user erasure, job retry, resync) change the store and follow the real state machine, so the
  demo shows correct governance; a reload resets it. `VITE_API_MODE=live` + `VITE_API_BASE_URL` turns MSW off.
- **Mock auth.** Login page with prefilled demo credentials; the user menu switches role (viewer / reviewer /
  owner) so visitors can see permissions change (viewer read-only, reviewer reviews and edits KB, owner
  manages settings, keys, members).
- **Fixtures** cover every status, risk tier, actor type and an end-to-end story: an episode → extracted
  memory → promoted → high-risk candidate → approved → verified KB entry v4 obsoleting v3 → synced, plus a
  failed sync job, a degraded provider, a KB gap from the Playground, a PII candidate, a GDPR erasure in the
  activity log.
- **Routing.** TanStack Router, file-based, URL holds namespace, filters, selected object (so the README can
  deep-link to "the review of cand_…").
- **Responsive.** Desktop-first (1280–1600); at phone width the tabs collapse into a menu and the drawer goes
  full-screen, so a README visitor on a phone can still look around.
- **Deploy.** Vercel project `llm-steward-admin`, deployed with the CLI (like AdForge), Root Directory
  `frontend/apps/admin`, SPA rewrite in `vercel.json`. Not Git-connected for now.
- **Backend hosting option kept.** The build is static, so design §10's "FastAPI serves the frontend" still
  works later; design §10 and §11 get a note that the Admin can also deploy standalone, and that a mock-data
  Admin shell was pulled ahead of P3–P5 at the owner's request.

## Verification

- `pnpm -C frontend typecheck && pnpm -C frontend lint && pnpm -C frontend test && pnpm -C frontend build`.
- Vitest: the mock store's state machine (approve a high-risk candidate → entry `verified`, old version
  `deprecated` with `superseded_by`; end-user confirm never yields `verified`; reject requires a reason;
  user erasure removes only that user's memories), formatting helpers, permission gating.
- Playwright (against the built app with MSW): the two §11 P6 acceptance flows on mock data — "review a
  high-risk candidate and see it synced" and "find a gap in the Playground and add it to the KB" — plus a
  smoke test that every page renders without console errors, and a viewer-role check that write actions are
  disabled.
- CI: a `frontend` job in `.github/workflows/ci.yml` running the same commands.
- Visual: Impeccable finish review on desktop (1440) and mobile (390) screenshots; `DESIGN.md` documented.
- Deployed URL returns the app and the e2e smoke passes against it.

## Left to the builder

Exact component breakdown, fixture contents beyond the story above, chart library choice for the Dashboard
(small and SSR-free), icon set (one library, consistent stroke), and how much of shadcn/ui is restyled.
