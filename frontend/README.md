# llm-steward frontend

pnpm workspace. `apps/admin` is the Admin SPA (design §9); `packages/widget` arrives in P7.

## Admin

React + Vite + TypeScript, TanStack Router (file routes in `src/routes/`) and Query, Tailwind 4, Radix primitives.
Visual direction "Standards Track": every governed object opens with an RFC-style header block, and following
Obsoletes / Obsoleted-by or a version opens that document on a stack (`src/components/DocumentStack.tsx`).
Contract in `.impeccable/surfaces/frontend-apps-admin.md`.

### Data

Pages call only `src/api/endpoints.ts`, which fetches `/admin/v1/...`.

| `VITE_API_MODE` | What answers `/admin/v1` |
| --- | --- |
| unset / `mock` (default, the Vercel demo) | MSW in the browser (`src/mocks/handlers.ts`) over an in-memory store (`src/mocks/store/`) that follows the governance rules; a reload resets it |
| `live` | the backend at `VITE_API_BASE_URL` (needs `/admin/v1` and Admin JWT from P5) |

The mock data is synthetic and every page says so. Demo login: any email, password `demo`, pick a role.

### Commands

```
pnpm install
pnpm dev          # http://127.0.0.1:8610
pnpm typecheck && pnpm lint && pnpm test && pnpm build
pnpm e2e          # builds, serves on 8611, runs Playwright (desktop + 390px smoke)
```

### Deploy

Vercel project `llm-steward-admin`, root `frontend/` (`vercel.json` builds only the Admin). From `frontend/`:
`vercel deploy --prod`.
