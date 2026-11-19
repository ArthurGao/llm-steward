---
version: 1
slug: "frontend-apps-admin"
primary_target: "frontend/apps/admin"
related_targets: []
---

# Surface brief — Admin SPA (`frontend/apps/admin`)

Mode: **Operate**. First release is a mock-data demo on Vercel for open-source visitors from the README;
later it runs against `/admin/v1`. Build path: code-led.

Audience and job: a developer evaluating llm-steward wants to see, in one minute, how a memory becomes
trusted knowledge — screened, reviewed, versioned, superseded, synced. Later: reviewers clearing the queue
by keyboard, owners checking providers and sync.

Constraints: mock data is labelled "Demo data" everywhere; no invented customers or metrics. UI copy in
English. Standard web controls only.

## Direction contract

THESIS: Every governed object reads like a standards-track document — a header block with Status, Risk,
Version, Obsoletes / Obsoleted-by and Source — and supersession is the navigation. Refuses the dark
sidebar + glowing accent + card dashboard that every dev tool ships.

OWN-WORLD: White paper ground, ink #141414, hairline #dcdcdc rules, one full ink rule under the top bar;
square corners, no decorative shadow. Status ladder colours: awaiting (amber #b26a00), active (ink),
verified (green #1e7f4f), deprecated (grey #6b6b6b), rejected (#b3261e); links #0b57d0. Red Hat Text for
UI, Red Hat Mono for header blocks, ids, numbers and diffs only.

STORY: The visitor opens a candidate, reads where it came from, why it is risky and what it would
obsolete, and understands that nothing becomes trusted without review — and nothing is ever deleted.

FIRST VIEWPORT: Top bar: mono wordmark, text tabs with an ink underline on the current one, namespace
select and "Demo data" on the right. Left 440px list of candidates (id, title, risk · source · age). Right:
the document — two-column mono header block, 22px bold title, screening note, unified diff against the
entry it obsoletes, status ladder, then Approve / Edit & approve / Reject (A / E / R).

FORM: Standards Track (IETF RFC status ladder and header discipline), position 1 of 7 on the ordered list
(Impeccable's pick over the roll). seed:0aa7c394ffad019f. Signature interaction: the Obsoletes /
Obsoleted-by links walk the supersession chain in place, each version opening as a stacked document.

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
