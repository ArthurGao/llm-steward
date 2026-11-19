# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

Fixed by `AGENTS.md`: React + Vite + TypeScript, TanStack Router/Query, shadcn/ui + Tailwind, Playwright,
pnpm workspace under `frontend/` (`apps/admin`, later `packages/widget`). The Admin is deployed to Vercel as
its own project, separately from the backend (confirmed 2026-11-09). The first version runs on mock data;
the backend's `/admin/v1` routes and Admin JWT do not exist yet (design §11, P5).

## Users

- **First audience (confirmed 2026-11-09):** open-source visitors and developers who reach the demo from
  the GitHub README and want to understand in a minute what llm-steward does.
- **Eventual users:** the team that operates an integrating app — reviewers who approve or reject
  knowledge candidates, owners who manage namespaces, keys and providers, viewers who audit.

## Product Purpose

llm-steward is a governance layer for AI memory and knowledge. It decides what an agent may remember,
what it may trust and what it retrieves: memories pass through screening, end-user confirmation and expert
review before they become trusted, traceable team knowledge. The Admin is where people see and act on that
governance: memories, knowledge-base entries, the review queue, retrieval, provenance and sync state.

Success for the demo: a developer opens it and understands the lifecycle — a memory is proposed, screened,
reviewed and becomes verified knowledge, with every step traceable.

## Positioning

It does not re-implement a memory or RAG engine; it governs mature ones (mem0, WeKnora, RAGFlow, lite-rag,
a native SQLite engine) through adapters. The differentiator is the explicit promote flow from memory to
KB, risk-tiered review, end-user confirmation that never yields "verified", and full provenance
(`change_log`, episodes, external refs).

## Operating Context

Pages and objects are defined in `docs/llm-steward-design.md` §9: Dashboard, Memories, Users, Knowledge
Base, Review Queue (diff against similar entries, keyboard a / e / r), Retrieval Playground, Activity Log,
Jobs, Settings, Providers. Namespace switcher on top, list + right-hand detail drawer. Roles: viewer,
reviewer, owner.

## Capabilities and Constraints

- Statuses: memory and KB entries `draft → active → verified`, `deprecated`, candidates `awaiting_user` /
  `awaiting_review`, `rejected`. Risk tiers low / medium / high. Actor types ai / end_user / admin / system /
  provider.
- All text in the UI is English (code-facing text rule); localisation is undecided.
- Undecided: license, TypeScript SDK.

## Brand Commitments

Name only: **llm-steward** (short form "Steward"). No logo, colour or typeface exists; design decides
(confirmed 2026-11-09).

## Evidence on Hand

No customers, testimonials, benchmarks or metrics exist. Demo data is mock and must be labelled as such;
never present it as real usage.

## Product Principles

1. Trust is earned, never assumed — the UI always shows how a piece of knowledge got its status.
2. Provenance over polish — every object links back to the episode and change that produced it.
3. Human judgement is the bottleneck — the review flow must be fast and keyboard-first.
4. Engines are swappable; governance state is not.
