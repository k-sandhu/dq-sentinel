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
| `app.html` | **Interactive prototype** — the full clickable system (11 connected screens). Direction + dark-mode switchers live in the topbar. |
| `components.html` | Component reference sheet (the Figma-style library) — every primitive in every direction. |
| `assets/app.css` | The whole design system: token-driven, three direction themes × light/dark. |
| `assets/app.js` | ~120 lines of vanilla JS: direction/theme switching (persisted), SPA routing, tabs, drawers. |

## Three directions for "the look"

The same product, three personalities — switch live from the topbar:

1. **Aurora** *(recommended)* — refined Metabase: airy, friendly, brand-blue, 12px radii, soft shadows. Broad/exec adoption.
2. **Graphite** — dense ops/terminal: teal accent, mono numerals, 6px radii, maximum density, dark-first. On-call data engineers.
3. **Editorial** — Linear/Notion: indigo, Inter, thin borders, generous whitespace. Modern premium SaaS feel.

Direction and theme are stored in `localStorage` and shared across all three HTML files.

## Screens in the prototype (`app.html`)

Overview · My work · Connections · Datasets · **Dataset detail** (tabbed: Profile /
Code / Lineage / Checks / Runs / Exceptions / RCA / Knowledge) · Checks · Exceptions
(with triage drawer) · Lineage · Query workbench · AI Assistant · Settings.

These map directly to the real backend routers and frontend pages described in
`AGENTS.md`, so they double as a layout spec for implementation.
