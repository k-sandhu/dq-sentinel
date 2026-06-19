# DQ Sentinel — system wireframes

Clickable, dependency-free wireframes for the data-quality platform. No build
step — open the files directly in a browser (or serve the folder).

```bash
# from repo root
python -m http.server -d wireframes 4173   # then open http://localhost:4173
# or just double-click wireframes/index.html
```

## Files

| File | What it is |
|---|---|
| `index.html` | **Start here.** Hub/gallery presenting the three visual directions side by side, with entry points. |
| `app.html` | **Interactive prototype (v2)** — the full clickable system (17 connected screens). Direction + dark-mode switchers live in the topbar. |
| `components.html` | Component reference sheet (the Figma-style library) — every primitive in every direction. |
| `COMPETITIVE-ANALYSIS.md` | Market scan of ~18 DQ/observability vendors — focus, differentiators, UX patterns, white space, and the v2 iteration plan. |
| `DESIGN-NOTES.md` | Editorial critique — what's good/bad in v1, what v2 adds, and what we deliberately **leave out** to avoid enterprise bloat. |
| `assets/app.css` | The whole design system: token-driven, three direction themes × light/dark. |
| `assets/app.js` | Vanilla JS: direction/theme switching (persisted), SPA routing, tabs, drawers. |

## Three directions for "the look"

The same product, three personalities — switch live from the topbar:

1. **Aurora** *(recommended)* — refined Metabase: airy, friendly, brand-blue, 12px radii, soft shadows. Broad/exec adoption.
2. **Graphite** — dense ops/terminal: teal accent, mono numerals, 6px radii, maximum density, dark-first. On-call data engineers.
3. **Editorial** — Linear/Notion: indigo, Inter, thin borders, generous whitespace. Modern premium SaaS feel.

Direction and theme are stored in `localStorage` and shared across all three HTML files.

## Screens in the prototype (`app.html`, v2 — 17 routes)

**Monitor:** Overview (inverted-pyramid: health ring, dimension scorecard, coverage/MTTD/MTTR/SLA,
90-day heatmap) · **Incidents** (list) · **Incident detail** (grouped alerts, impact, timeline) ·
Exceptions · My work.
**Build:** Connections · Datasets · **Dataset detail** (tabbed: Profile / Code / Lineage / Checks /
Runs / Exceptions / **RCA** / Contract / Knowledge) · Checks · **Generate checks** (AI review +
exploration-agent SQL trace + checks-as-code) · **Contracts** (registry + editor + breaking-change
diff + CI gate) · **Lineage** (neighborhood/hop + impact panel).
**Investigate:** Assistant (acts, not just answers) · Workbench · **Alerts & routing** (rules +
interactive Slack) · Settings · **Status page** (read-only stakeholder view).

The upgraded **exception drawer** adds severity, assignee, and a good-vs-bad-rows + column-attribution
"why it failed" view.

These map directly to the real backend routers and frontend pages described in `AGENTS.md`, so they
double as a layout spec. The v2 additions (Incidents, Contracts, Alerts, Status, AI check-review) are
informed by `COMPETITIVE-ANALYSIS.md`; the rationale for what's in/out is in `DESIGN-NOTES.md`.
