---
name: llm-steward Admin
description: Governance for AI memory and knowledge, laid out as a standards-track document set.
colors:
  ink: "#141414"
  link: "#0b57d0"
  awaiting: "#955800"
  awaiting-wash: "#fdf3e2"
  verified: "#1e7f4f"
  verified-wash: "#e7f3ec"
  deprecated: "#6b6b6b"
  rejected: "#b3261e"
  rejected-wash: "#fcebea"
  del: "#8c1d18"
  del-wash: "#fdecea"
  ins: "#14532d"
  ins-wash: "#e3f2e8"
  paper: "#ffffff"
  wash: "#f6f6f4"
  select: "#eef3fc"
  ink-2: "#4f4f4f"
  ink-3: "#666666"
  rule: "#dcdcdc"
typography:
  display:
    fontFamily: "Red Hat Text, ui-sans-serif, system-ui, sans-serif"
    fontSize: "22px"
    fontWeight: 700
    lineHeight: 1.375
  headline:
    fontFamily: "Red Hat Text, ui-sans-serif, system-ui, sans-serif"
    fontSize: "18px"
    fontWeight: 700
    lineHeight: 1.5
  title:
    fontFamily: "Red Hat Text, ui-sans-serif, system-ui, sans-serif"
    fontSize: "17px"
    fontWeight: 700
    lineHeight: 1.5
  body:
    fontFamily: "Red Hat Text, ui-sans-serif, system-ui, sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.5
  label:
    fontFamily: "Red Hat Text, ui-sans-serif, system-ui, sans-serif"
    fontSize: "13px"
    fontWeight: 500
    lineHeight: 1.5
  caption:
    fontFamily: "Red Hat Text, ui-sans-serif, system-ui, sans-serif"
    fontSize: "12.5px"
    fontWeight: 400
    lineHeight: 1.5
  mono:
    fontFamily: "Red Hat Mono, ui-monospace, Menlo, monospace"
    fontSize: "13px"
    fontWeight: 400
    lineHeight: 1.85
    fontFeature: "tnum"
  wordmark:
    fontFamily: "Red Hat Mono, ui-monospace, Menlo, monospace"
    fontSize: "15px"
    fontWeight: 500
    lineHeight: 1.5
rounded:
  none: "0px"
spacing:
  gutter-mobile: "20px"
  gutter: "28px"
  document-gutter: "32px"
  cell-x: "12px"
  cell-y: "10px"
  stack: "24px"
  topbar: "52px"
components:
  button-primary:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.paper}"
    rounded: "{rounded.none}"
    padding: "0 14px"
    height: "36px"
  button-primary-hover:
    backgroundColor: "#2b2b2b"
  button-secondary:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    rounded: "{rounded.none}"
    padding: "0 14px"
    height: "36px"
  button-secondary-hover:
    backgroundColor: "{colors.wash}"
  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.ink}"
    rounded: "{rounded.none}"
    padding: "0 14px"
    height: "36px"
  button-danger:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.rejected}"
    rounded: "{rounded.none}"
    padding: "0 14px"
    height: "36px"
  button-danger-hover:
    backgroundColor: "{colors.rejected-wash}"
  button-disabled:
    backgroundColor: "{colors.wash}"
    textColor: "{colors.ink-3}"
  button-sm:
    typography: "{typography.label}"
    padding: "0 10px"
    height: "32px"
  input:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    rounded: "{rounded.none}"
    padding: "0 10px"
    height: "36px"
  header-block:
    typography: "{typography.mono}"
    textColor: "{colors.ink}"
  diff-caption:
    backgroundColor: "{colors.wash}"
    textColor: "{colors.ink-2}"
    typography: "{typography.mono}"
    padding: "6px 14px"
  diff-removed:
    backgroundColor: "{colors.del-wash}"
    textColor: "{colors.del}"
    typography: "{typography.mono}"
    padding: "6px 14px"
  diff-added:
    backgroundColor: "{colors.ins-wash}"
    textColor: "{colors.ins}"
    typography: "{typography.mono}"
    padding: "6px 14px"
  table-row-selected:
    backgroundColor: "{colors.select}"
  page-title:
    typography: "{typography.headline}"
    padding: "14px 28px"
  tooltip:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.paper}"
    rounded: "{rounded.none}"
    padding: "6px 10px"
---

# Design System: llm-steward Admin

## Overview

**Creative North Star: "The Standards Track"**

Every governed object is a document in a standards series. It opens with a two-column monospaced header block (Status, Risk, Obsoletes, Source), carries a bold plain-language title, shows its change as a unified diff, and sits on a status ladder that says how far it has come toward trusted knowledge. Following Obsoletes / Obsoleted-by is how you move around: each linked version opens as a stacked document above the current one, and the one beneath folds into a spine you can click to go back to.

The material is paper and ink. The ground is white, text is near-black, structure comes from hairline rules, and one full-weight ink rule closes the top bar. Corners are square everywhere. Resting surfaces have no shadows. Colour carries meaning only: one link blue, and a status ladder of amber, green, grey and red that appears on text, on small square marks and on the borders of ladder rungs. The layout is dense and built for working through a queue: a list on the left, the open document on the right, single-key actions.

The world rejects the dark sidebar, the glowing accent and the dashboard made of cards that most developer tools ship. Even the dashboard is a document: a mono header block, ruled lists and flat bar charts drawn in ink and grey.

**Key Characteristics:**
- Paper ground, ink text, hairline rules; one ink rule under the top bar.
- Square corners, no radius anywhere; status marks are small squares.
- Monospace for machine-readable values (header blocks, ids, keys, numbers, timestamps, diffs, wordmark); Red Hat Text for everything people read.
- Colour is reserved for the status ladder, diffs and links.
- Supersession navigation through stacked documents.

## Colors

The palette is achromatic, plus one link blue and a four-step status ladder. Each ladder colour has a pale wash for filled states.

### Primary
- **Document Ink** (ink): body text, primary buttons, the top bar's closing rule, the current-tab underline, table header rules, and the "active" status. Ink means a settled fact.

### Secondary
- **Reference Blue** (link): links, focus outlines, and focused input borders. It is the only interactive hue; buttons stay ink.

### Tertiary: the status ladder
- **Awaiting Amber** (awaiting, wash awaiting-wash): candidates awaiting review or user confirmation, medium risk, processing, degraded, and the current rung of the ladder. The contract specified #b26a00. At 4.0:1 on white that fails AA for 13px status text, so the build uses a darker value (5.6:1), recorded in the stylesheet.
- **Verified Green** (verified, wash verified-wash): verified, accepted, synced, healthy, ready, done.
- **Obsolete Grey** (deprecated): deprecated, superseded, expired, deleted.
- **Rejection Red** (rejected, wash rejected-wash): rejected, failed, down, high risk, error notes, the danger button, and PII warnings.
- **Diff Removed / Added** (del + del-wash, ins + ins-wash): unified-diff lines only. Darker than the ladder red and green so long mono lines stay readable on their washes.

### Neutral
- **Paper** (paper): the page and every resting surface.
- **Wash** (wash): hover fills, diff captions, document-stack spines, the mobile sub-bar, disabled buttons.
- **Selection Tint** (select): the selected row in a list, and spine hover.
- **Secondary Ink** (ink-2): meta text, counts, hints, inactive tabs, table headers.
- **Tertiary Ink** (ink-3): header-block labels, placeholders, upcoming ladder rungs, disabled text, the Demo-data dashed border.
- **Hairline** (rule): every divider, row rule, input border and ladder rung border.

### Named Rules
**The Ladder-Only Colour Rule.** Hue appears only to state a status, a risk, a diff or a link. A new surface that wants colour for any other reason uses ink, grey or wash.

**The Settled-Is-Filled Rule.** A status mark is a 8px square with a 1.5px border. It is filled once the state is settled (active, verified, rejected) and hollow while open (awaiting, deprecated, neutral).

## Typography

**Body Font:** Red Hat Text (400, 500, 700), with ui-sans-serif, system-ui fallback
**Mono Font:** Red Hat Mono (400, 500), with ui-monospace, Menlo fallback

**Character:** A plain, slightly technical humanist sans for prose and controls, paired with its monospaced sibling for anything a machine issued. The two share a skeleton, so mixing them on one line (a status sentence with an id in it) reads as one voice.

### Hierarchy
- **Display** (700, 22px, snug 1.375): the title of an open document (candidate statement, KB entry title, sign-in heading), capped at 60ch. Memory and user documents use the same treatment at 20px.
- **Headline** (700, 18px): page titles in the page-title strip, followed by a count in secondary ink.
- **Title** (700, 17px): section headings on Providers and dialog titles. In-document sub-sections (Versions, Supersession chain) are bold at body size.
- **Body** (400, 14px, 1.5): all reading text. Prose blocks are capped at 68–72ch. Headings use balanced wrapping, paragraphs pretty wrapping.
- **Label** (500, 13px): status badges, field labels, ladder rungs, small buttons.
- **Caption** (400, 12.5px): table headers, hints, field errors, ladder notes, tooltips, spines.
- **Mono** (400, 13px, 24px line): header blocks, ids, keys, timestamps, similarity scores, counts, chart axes, diffs. Tabular numerals.

### Named Rules
**The Machine-Voice Rule.** Anything the system issued (an id, a key, a version, a timestamp, a score, an email or a diff) is set in Red Hat Mono. Sentences people read are never set in mono.

**The Bold-Not-Big Rule.** Hierarchy comes from weight (700 against 400/500) inside a narrow 12.5–22px range. Nothing exceeds 22px.

## Layout

The page is a full-width application frame, not a centred column. A sticky 52px top bar holds the mono wordmark, text tabs (shown from 1280px up; below that a menu button), the namespace select, the Demo-data marker and the account menu. Below 640px, a wash sub-bar under the top bar carries the namespace select and "Demo data · resets on reload".

Under that, a page-title strip (14px vertical padding, 28px gutter, 20px on mobile) is closed by a hairline. List pages then use a split view: from 1024px up, a list column of 340–440px with a hairline on its right and the document on the right, each scrolling on its own. Below 1024px the two take turns, and an open document gets a "Back to the list" link. Documents are capped at 920px wide, with 32px gutters at desktop widths and 20px on mobile.

The rhythm is dense. Table cells are 12px by 10px, and the outer cells pick up the page gutter. Document parts are 20–24px apart (header block, title, notes, diff, ladder, actions). The header block switches from two columns to one below 640px, and the status ladder switches from a horizontal run to a vertical stack.

**The Two-Pane Rule.** A list of governed objects always opens its item in the right-hand document pane, never in a modal or a new page, so the list and the document can be read together.

## Elevation & Depth

Resting surfaces are flat. Depth comes from rules: hairlines between rows and sections, one ink rule under the top bar and under table headers, and tonal fills (wash, select) for hover and selection. Shadows appear only on transient layers that float above the page: dropdown menus and dialogs. Even those keep a 1px ink border, so the shadow only separates the layer and never stands in for its edge.

### Shadow Vocabulary
- **Menu lift** (`box-shadow: 0 8px 24px rgba(0,0,0,0.12)`): account and mobile page menus.
- **Dialog lift** (`box-shadow: 0 12px 40px rgba(0,0,0,0.18)`): modal dialogs, over a 30% ink scrim.

### Named Rules
**The Flat-At-Rest Rule.** Nothing that stays on the page has a shadow. A shadow means "this layer will go away".

## Shapes

Every corner is square: buttons, inputs, selects, dialogs, menus, tooltips, the diff frame, ladder rungs and status marks. Borders are 1px. The secondary button, the primary tab underline and the top-bar rule use ink, and structure uses hairline. The one non-solid border is the dashed ink-3 outline of the Demo-data marker, which marks synthetic content. Status ladder rungs share their borders (they overlap by 1px), so the ladder reads as one segmented bar. The current or stopping rung is raised above its neighbours so its amber or red border shows on all four sides.

**The Square Rule.** Corner radius is 0. A rounded corner anywhere means a component did not come from this system.

## Components

### Buttons
Ink, square and quiet, with no animation beyond a 100ms colour change.
- **Shape:** square, 1px border, 36px tall (32px small), medium weight.
- **Primary:** ink fill, paper text. Hover lifts the fill to #2b2b2b. One per action group (Approve, Save and approve).
- **Secondary (default):** paper fill, ink border and text. Wash on hover.
- **Ghost:** no border or fill until hover. Used for Cancel.
- **Danger:** paper fill with rejected-red border and text, red wash on hover. Used only to confirm a destructive decision.
- **Disabled:** wash fill, hairline border, ink-3 text, not-allowed cursor. When the role forbids the action, an ink tooltip explains why.
- **Shortcut hint:** a single key in 11px mono at 70% opacity after the label (Approve A, Edit & approve E, Reject… R).

### Inputs / Fields
- **Style:** paper fill, hairline border, square, 36px tall, 10px inline padding. Textareas have a 96px minimum height and 24px line height.
- **Hover / Focus:** the border darkens to ink-3 on hover and turns link blue on focus. The global 2px link-blue focus outline sits 2px outside.
- **Field:** a 13px medium label above the control and a 12.5px hint in secondary ink below. An error replaces the hint in rejected red with role="alert".

### Navigation
- **Top tabs:** 14px medium text in secondary ink. Hover goes to ink. The current tab is ink with a 2px ink underline sitting on the top bar's rule. The Review tab carries its pending count in amber mono.
- **In-page tabs:** the same grammar on a hairline: 24px apart, with a 2px ink underline on the active one.
- **Mobile:** a square menu button opens an ink-bordered list of the same pages. The current page is bold.

### Status Badge
Text in its ladder colour (13px medium) after an 8px square mark that is filled when settled and hollow when open (see the Settled-Is-Filled Rule). The same component renders risk (high red, medium amber, low secondary ink).

### Header Block (signature)
The RFC header every governed object opens with: label/value pairs in two 13px mono columns. Labels are ink-3 ("Status:", "Obsoletes:"), values are ink. The right column is right-aligned. A hairline and 16px of padding close the block. Obsoletes / Obsoleted-by values are links that open the stacked document.

### Status Ladder (signature)
A horizontal run of rungs on shared hairline borders, each at least 112px wide. Done rungs are ink text, the current rung is bold amber with an amber border, a stopped rung (rejected, expired) is bold red with a red border, and upcoming rungs are ink-3. Each rung can carry a 12.5px note (who, how risky).

### Diff View (signature)
A hairline-framed figure in 13px mono. A wash caption names the versions ("KB-0142 v3 → proposed"). Below it are one removed line (del on del-wash, prefixed "- ") and one added line (ins on ins-wash, prefixed "+ "). Changed words are struck through or highlighted at 15% of the line colour.

### Document Stack (signature)
Opening an older version or an obsoleted entry stacks it above the current document. Every document underneath collapses into a full-width wash spine (12.5px mono, a corner-up-left icon) that returns to that level when clicked. The new top document slides up 10px and fades in over 180ms (cubic-bezier(0.16, 1, 0.3, 1)). Escape closes it, and reduced motion removes the movement.

### Data Table
Dense rows with an ink rule under the 12.5px header row and hairlines between rows. Rows that open something use a pointer cursor, a wash hover and a selection-tint fill when selected. The keyboard moves with j / k or the arrow keys, and Enter opens.

### Demo-data Marker
A 12px secondary-ink label in a dashed ink-3 border, always in the top bar (or the mobile sub-bar). Synthetic content is never unlabelled.

## Do's and Don'ts

### Do:
- **Do** open every governed object with the mono header block, then a bold title of 22px or less, then the diff and the status ladder, in that order.
- **Do** state status with the Status Badge: ladder colour text and a square mark, filled once settled.
- **Do** set ids, keys, versions, timestamps, scores and diffs in Red Hat Mono with tabular numerals.
- **Do** separate with hairlines (rule) and close the top bar and table headers with ink.
- **Do** keep 13px status text on the darkened awaiting amber (#955800), not the contract's #b26a00.
- **Do** label demo content with the dashed Demo-data marker.

### Don't:
- **Don't** round a corner. Radius is 0 everywhere.
- **Don't** put a shadow on a resting surface. Shadows belong to menus and dialogs only.
- **Don't** use a ladder colour, link blue or a wash for decoration, emphasis or branding.
- **Don't** build the dark sidebar, glowing accent or grid of metric cards this world rejects.
- **Don't** set prose, headings or button labels in mono, or a machine-issued value in the sans.
- **Don't** open a list item in a modal when the two-pane split view applies.
