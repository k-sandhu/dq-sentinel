# DQ Sentinel — Design Philosophy

The single, holistic statement of *how* DQ Sentinel should look, feel, and behave —
and *why*. The wireframes in this folder are this document made clickable. Where
`COMPETITIVE-ANALYSIS.md` explains what the market does and `DESIGN-NOTES.md` argues
what to build, this file defines the durable principles everything else ladders up to.

---

## 1. North star

> **Open, self-hostable data quality with agents that *investigate* your data — not
> just watch it. Read-only by design, your LLM or ours, across 9 engines.**

Every screen, empty state, and default should make that sentence feel true. Three ideas
inside it drive the design:

- **Investigate, don't just alert.** The product's job isn't to throw anomalies over the
  wall; it's to help a human understand and resolve them. The UI is built around a
  *triage-to-resolution* arc, not a wall of red.
- **Trust through transparency.** Agents show their work (the SQL they ran, the steps they
  took, the rows that failed and *why*). Nothing is a black box. Read-only is a visible
  promise, not fine print.
- **Yours to shape.** It runs in the user's environment with the user's model — so the UI,
  too, is theirs to shape (theme, density, layout). Opinionated defaults, generous control.

---

## 2. Principles

1. **Answer "are we OK?" in five seconds.** Every overview leads with status, then trend,
   then the worklist (the "inverted pyramid"). A viewer should know the health of their data
   before they scroll.
2. **One object, all its facets.** A dataset, an incident, a check — each has a single home
   that gathers everything about it (profile, checks, runs, exceptions, RCA, lineage,
   contract). Don't scatter an object across the app.
3. **Progressive disclosure.** Summary on the surface, detail one click deeper (a row → a
   drawer; a node → a side panel; a KPI → a filtered list). Cards stay scannable; depth lives
   in drawers and detail pages.
4. **Show the work.** AI and ML output is always accompanied by its evidence — streamed
   steps, the SQL trail, good-vs-bad row samples, attribution. Confidence is earned, not asserted.
5. **Calm by default, dense on demand.** The resting state is legible and unhurried; power
   users can compress it (Compact density, Icons-only nav) without losing anything.
6. **Restraint is a feature.** We are a *quality* tool, not a data catalog, a FinOps console,
   or an MDM suite. Saying no keeps the product sharp (see §11).
7. **Accessible is non-negotiable.** Contrast, focus, keyboard reach, and reduced-motion are
   designed in, not bolted on.

---

## 3. The system is token-driven

Everything visual resolves from CSS custom properties on `:root`. The same markup re-skins
instantly because components never hard-code a colour, radius, or spacing — they read tokens.

This is the core architectural decision and it pays for itself constantly: a new theme is a
set of token values, not a stylesheet; a user preference is an attribute on `<html>`; the
real React frontend (`frontend/src/styles.css`) already uses the same token names, so these
wireframes are a layout-and-token spec, not a throwaway mock.

Six composable axes, each a `data-*` attribute on `<html>` (persisted per-device):

| Axis | Attribute | Drives |
|---|---|---|
| **Theme** | `data-dir` | brand accent, corner radius, type family, surface palette |
| **Mode** | `data-theme` | light / dark surfaces (each theme has both) |
| **Density** | `data-density` | spacing, control heights, font size |
| **Font** | `data-font` | interface type family (overrides the theme default) |
| **Navigation** | `data-nav-layout` | full sidebar / icons-only / centered top-nav |
| **Accent** | inline `--brand` | brand colour; everything else derives from it |

They're **orthogonal on purpose**: a user can run Graphite + Light + Comfortable + Centered if
that's what they like. Personality (theme) is separated from ergonomics (density, layout) so
neither dictates the other.

---

## 4. The three themes

Not skins — three *personalities*, each for a real audience. We deliberately ship **three**,
not a paint-store of seven: enough to fit different orgs, few enough that each is designed,
not generated.

| Theme | Feel | Built for | Signature tokens |
|---|---|---|---|
| **Aurora** *(default)* | Refined, airy, friendly — evolved Metabase | Broad / exec adoption | brand `#2f8fe6`, radius 12px, Segoe/system |
| **Graphite** | Dense, technical, terminal-sharp | On-call data engineers | brand teal `#2fb6a3`, radius 6px, mono numerals |
| **Editorial** | Calm, premium, generous whitespace — Linear/Notion | Modern SaaS brand | brand indigo `#5b5bd6`, radius 10px, Inter |

A theme defines **brand + shape + type + surfaces**. It does *not* own density anymore — that's
a user axis — so "Graphite feels dense" is now the user's Compact choice, while Graphite keeps
its identity (teal, sharp corners, mono). This is why the same prototype can demo three
distinctly different products from one codebase.

### Each theme has a real light *and* dark mode

Dark mode is **not** one global inversion. Each theme carries a tailored dark surface set so it
stays itself in the dark:

- **Aurora dark** — blue-slate (`#0f1620` bg, `#18212e` cards): cool and clean.
- **Graphite dark** — near-black (`#0b0e11` bg, `#14181d` cards): the ops/terminal look.
- **Editorial dark** — indigo-charcoal (`#14131a` bg, `#1c1b24` cards): warm and premium.

`System` mode follows the OS via `prefers-color-scheme` and switches live.

---

## 5. Colour & status semantics

- **Brand is per-theme; status is universal.** `--ok / --warn / --danger / --info / --accent`
  are *identical across every theme and both modes* (with mode-appropriate tints). A failing
  check is the same red whether you're in Aurora light or Graphite dark — muscle memory and
  trust depend on it. Only the brand and surfaces change.
- **Accent derives from one value.** Themes (and the user's accent picker) set `--brand`; the
  rest — `--brand-2`, `--brand-deep`, `--brand-ghost` — are computed with `color-mix()`. Pick a
  colour, the whole system re-harmonises. Status colours are never overridden by accent.
- **Traffic-light, consistently.** Green = healthy/passing, amber = at-risk/degraded, red =
  failing. Arrows and deltas follow the same rule (green-up = good).
- **Tints, not just text.** Status reads as a soft background pill (`--*-bg`) with a legible
  foreground (`--*-fg`), so it survives next to dense data without shouting.

---

## 6. Typography

- **System-first, optionally Inter or Rounded.** Default stacks are fast and native; the user
  can opt into Inter (crisp, neutral) or a rounded stack (softer). Graphite leans on a mono
  family for numerals because data people read tables, not prose.
- **Tabular numerals** everywhere numbers line up (KPIs, tables, metrics) so columns don't dance.
- **Headings carry weight, not size alone.** `--head-weight` and tight `--head-spacing` give
  hierarchy without large type — important in a dense, table-heavy product.

---

## 7. Density & spacing (the ergonomics axis)

Spacing is a *user* decision, because a data analyst scanning 200 rows and an exec reading a
scorecard want opposite things. One axis scales six tokens together so the whole UI breathes
or compresses coherently:

| Token | Compact | Cozy (default) | Comfortable |
|---|---|---|---|
| `--pad` (card padding) | 13px | 18px | 26px |
| `--gap` (grid gap) | 10px | 15px | 22px |
| `--row-h` (table row) | 32px | 40px | 50px |
| `--ctl-h` (buttons/inputs) | 31px | 36px | 44px |
| `--fs` (base font) | 12.5px | 13.5px | 15px |
| `--card-pad` | 14px | 18px | 26px |

Because controls and type scale with spacing, Compact stays *tight and usable* (not just
cramped) and Comfortable stays *roomy and legible* (not just big). Radius is a theme trait, so
density never fights the theme's shape language.

---

## 8. Components & composition

- **Cards are the substrate.** Surfaces, borders, soft shadows, consistent radius. Content is
  composed from a small, sturdy kit: cards, KPI tiles, pills/badges, tables, tabs, segmented
  controls, meters, sparklines, timelines, drawers. See `components.html` for the full library
  in every theme.
- **Tables are first-class.** This is a data product; tables get real care — tabular numerals,
  hover affordance, sticky headers, status cells, row-as-link. Density tuning lives or dies here.
- **Drawers over modals for detail.** Investigating an exception, editing a check, or tuning
  appearance happens in a right-side drawer that keeps context visible behind it. Modals are
  reserved for true decisions (e.g. confirming a promote-to-incident grouping).
- **Charts are crisp and themed.** A tiny SVG engine (area/line/bars, optional comparison series
  and expected band) draws with CSS-variable colours, so every chart recolours with the theme —
  no chart library, no off-brand palette.

---

## 9. Information architecture

Three nav groups, and only three, so the sidebar never reads as enterprise sprawl:

```
Monitor      Overview · Incidents · Exceptions · My work
Build        Connections · Datasets · Checks · Contracts · Lineage
Investigate  Assistant · Workbench · Alerts & routing
```

- **Cross-dataset concerns get a top-level home** (Incidents, Contracts, Lineage). Everything
  *about one dataset* is a tab inside that dataset, not a nav item.
- **The triage arc is the spine:** Overview → Exceptions/Incidents → dataset detail → RCA →
  resolve. The whole product is organised around moving an issue from "noticed" to "closed,"
  with the knowledge base learning from each resolution.
- **Layout flexes** (full sidebar / icon rail / centered top-nav) without changing the IA.

---

## 10. Interaction, motion, and voice

- **Motion is functional.** Drawer slide, the live "agent is investigating" pulse, hover lifts —
  each communicates state or causality. Everything respects `prefers-reduced-motion`.
- **Optimistic and immediate.** Preferences apply instantly and persist; triage actions reflect
  across the app at once. The UI feels like a tool you're operating, not a form you're submitting.
- **Voice is plain and operational.** Labels say what happens ("Promote to incident", "Mark
  expected"). Empty states never dead-end — they point to the next best action and quietly
  reinforce the north star.

---

## 11. What we deliberately leave out

Restraint is part of the philosophy. We don't build (and the wireframes don't pretend to):

- compute / cost / FinOps dashboards,
- a full data catalog or business glossary,
- MDM / reference-data / stewardship suites,
- any agent that **writes to a source** (everything is read-only via `guard_sql()` — agents
  *recommend and investigate*, humans decide),
- "monitor literally everything" onboarding (we profile → recommend → human approves).

The rationale is in `DESIGN-NOTES.md §"What should NOT be there"`. These omissions are what
keep DQ Sentinel a focused quality tool instead of a sprawling platform.

---

## 12. Accessibility

- **Contrast** is designed into every theme/mode pairing, including the status tints over
  composited dark surfaces.
- **Focus is always visible** (`--ring`), and the whole prototype is keyboard-reachable.
- **Semantics first:** drawers, tabs, and dialogs use real roles/labels; icon-only controls keep
  accessible names.
- **Reduced motion** is honoured everywhere animation appears.
- **Type and spacing are user-tunable**, which is itself an accessibility feature.

---

## 13. How this maps to the real app

These wireframes intentionally mirror the production system so they're a spec, not art:

- The token names match `frontend/src/styles.css`; themes/density/accent can be lifted in directly.
- The screens map to real routers/pages (`backend/app/api/*`, `frontend/src/pages/*`) per
  `AGENTS.md`, so the IA and component choices are implementable as-is.
- The standalone builds in `standalone/` are generated from these sources, so "what was
  reviewed" and "what's in the repo" never drift.

---

### Companion documents
- `COMPETITIVE-ANALYSIS.md` — the market scan that informed these choices.
- `DESIGN-NOTES.md` — the opinionated critique (what's good/bad, what to add, what to cut).
- `README.md` — how to run and what each file is.
