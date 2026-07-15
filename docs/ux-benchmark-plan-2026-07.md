# DQ Sentinel — UX Benchmark Plan (July 2026)

> **What this is.** The *pre-registered* test plan for a live, browser-driven UX benchmark
> of DQ Sentinel at `main` HEAD (`cda2e16`). It defines (1) what "good UX" means for this
> product, (2) the scoring rubric, and (3) every test that will be run, with the expected
> outcome written down *before* execution. Results are reported separately in
> [`ux-benchmark-report-2026-07.md`](ux-benchmark-report-2026-07.md).
>
> **Relationship to existing docs.** [`UI-GLOSSARY.md`](UI-GLOSSARY.md) is a static,
> code-derived click map (last reviewed 2026-06-19, pre-#218). This benchmark is an
> *empirical* pass: it drives the real app in a real browser with seeded data and scores
> lived experience, not wiring. Functional bugs already on file (#256–257, #261–275) are
> out of scope and will be deduped, not re-reported; this pass hunts **experience defects**.

---

## 1. Environment & method

| Item | Value |
|---|---|
| Build under test | Docker demo of `main@cda2e16` (post-#218 audit remediation) |
| UI / API | `http://localhost:3002` / `http://localhost:18002` |
| Data | Seeded demo DB: 4 connections, 13 datasets, checks/runs/exceptions history, curated catalog |
| Account | `admin@example.com` (admin role — sees all controls; role-gate checks are spot checks only) |
| LLM | **Deliberately disabled** (`DQ_LLM_MODEL` empty) — this is a first-class test condition: every AI surface must degrade gracefully and visibly, per epic #66 |
| Method | Scripted browser walkthrough per area; screenshot evidence at each decision point; keyboard-only and dark-mode sweeps as separate passes |
| Evidence | Screenshots archived with the report; findings cite screen + reproduction path |

**Known-bug dedupe list (do not re-file):** #256 (heuristic proposals normalize dirt),
#257 (manual+scheduler double-run), #261 (catalog volume → scheduled checks error),
#262 (errored checks → health fail w/ 0 exceptions), #263–266, #267 (P0 DuckDB file read),
#268–275. Where one of these *surfaces* as bad UX, the UX consequence may still be scored
(e.g. #262's "failing with nothing to triage" is an honesty problem even after the
functional fix).

---

## 2. What "good UX" means for DQ Sentinel

DQ Sentinel is an **enterprise data-quality monitoring console**. The owner's standing
design bar: *design for the primary users — DQ analysts who live in the tool all day*
(epic #66). Good UX here is therefore not "friendly onboarding for novices"; it is
**operational excellence for professionals**, in the class of Datadog, Linear, or Monte
Carlo. Concretely, three personas anchor every judgment:

- **P1 — The DQ analyst (primary).** Opens the app every morning, works a queue of
  exceptions all day. Cares about: *time-to-triage* (alert → understanding → action in
  seconds), information density without clutter, keyboard operability, never losing triage
  state, and links that always carry context forward (no re-filtering by hand).
- **P2 — The data engineer (secondary).** Doesn't live in the tool; arrives cold via an
  assignment or a deep link pasted in Slack. Cares about: the linked page being
  self-sufficient (what dataset, what failed, since when, what changed), and a fast path
  to the offending rows/SQL.
- **P3 — The lead / stakeholder (tertiary).** Glances weekly. Cares about: a status page
  that is legible in 10 seconds, numbers that agree with each other everywhere, honest
  time labels, and exports that can go in a slide.

From these personas plus recognized heuristics (Nielsen) and the epic #66 cross-cutting
standards, the benchmark scores **eight pillars**:

| # | Pillar | What it demands here (the bar) |
|---|---|---|
| **U1** | **Orientation & information architecture** | A first-time P2/P3 forms the right mental model in <30s. Nav groups match the domain vocabulary (sources → checks → runs → exceptions → incidents). Every page answers "where am I, what is this, what can I do" without a manual. |
| **U2** | **Workflow efficiency** | The core triage loop (see failing thing → inspect evidence → assign/resolve → next) takes minimal clicks, supports bulk action, and never forces a context rebuild. Frequent actions are one interaction away; pagination/filters persist. |
| **U3** | **Findability & information scent** | Anything nameable (dataset, check, connection) is reachable via global search/palette in <5s from anywhere. Every entity mention is a link; counts are drill-downable; no dead ends ("42 exceptions" must be clickable to those 42). |
| **U4** | **System status & feedback** | Every async action gives immediate feedback (spinner/optimistic/toast). Loading, empty, and error states are all designed — an empty table says *why* it's empty and what to do next. Long operations (runs, profiling) show progress and completion. |
| **U5** | **Error prevention & recovery** | Destructive actions confirm with specifics ("delete check X and its 213 exceptions?"). Forms validate before submit with field-level messages. Analyst triage state is never silently destroyed (#66 standard). Failed API calls surface actionable errors, not blank screens. |
| **U6** | **Consistency & standards** | Same concept = same word everywhere (glossary terms: check, run, exception, incident). Same problem = same pattern (all tables filter/sort/paginate alike; all detail pages share anatomy). Status colors/badges mean the same thing on every screen. |
| **U7** | **Accessibility & keyboard** | WCAG AA intent: visible focus rings, full keyboard path through the primary triage flow, labeled controls, AA contrast in both themes, no information conveyed by color alone (pass/fail must have text/icon too). |
| **U8** | **Trust, honesty & degradation** | Time labels honest and unambiguous (UTC / "last 24h", never a bare "today"). The same metric shows the same value on every screen. Disabled capability (LLM off) is *visibly* off with an explanation — never a silent no-op or a spinner forever. Charts don't lie (axes, baselines). |

### Scoring rubric

Each test area is scored on its applicable pillars, 1–5:

| Score | Meaning |
|---|---|
| 5 | Exemplary — competitive with best-in-class ops tools; nothing to fix |
| 4 | Good — minor polish gaps that don't slow a pro user |
| 3 | Adequate — works, but friction a daily user would curse at |
| 2 | Poor — a core expectation of the pillar is unmet; workarounds needed |
| 1 | Broken — the pillar fails; user is blocked, misled, or loses work |

Individual findings carry severity: **P0** (blocks/misleads on core flow), **P1** (major
friction on core flow, or any data-honesty violation), **P2** (friction on secondary
flow / polish with user impact), **P3** (cosmetic).

---

## 3. Test areas & scripts

Each test case states the **action** and the **pre-registered expectation**. Anything
that deviates becomes a finding.

### A1 — Login & first contact (`/login`) — pillars U1 U4 U5 U7

| # | Test | Expectation |
|---|---|---|
| A1.1 | Load `/login` cold | Page renders <2s; product name + one-line value statement visible; no console errors |
| A1.2 | Submit empty form | Field-level validation; no network call; focus moves to first invalid field |
| A1.3 | Wrong password | Clear inline error ("invalid credentials"), form intact (email preserved), no stack traces |
| A1.4 | Valid login | Redirect to landing page <2s; session persists on reload |
| A1.5 | Keyboard-only | Tab order: email → password → submit; Enter submits; focus visible throughout |
| A1.6 | Deep link while logged out (`/checks`) | Redirect to login, then **return to `/checks` after auth** (not dumped at home) |

### A2 — Home (`/`) — U1 U3 U4 U8

| # | Test | Expectation |
|---|---|---|
| A2.1 | First paint after login | A scorecard answering "is my data healthy today?" in one glance; worst things first |
| A2.2 | Every number/tile | Clickable → drills into the filtered list backing that number; counts agree with the target page |
| A2.3 | Time labels | Every widget states its window ("last 24h", UTC-honest); no bare "today" |
| A2.4 | Empty/degraded regions | Any widget with no data explains itself; no perpetual spinners or NaNs |
| A2.5 | Health pill (sidebar) | Matches Home's verdict; click → a sensible drill-down |

### A3 — My Work (`/my-work`) — U2 U3 U4

| # | Test | Expectation |
|---|---|---|
| A3.1 | Open as analyst with assignments | Personal queue: assigned exceptions/incidents, ordered by urgency; zero-state explains how work arrives |
| A3.2 | Item click | Lands on the exact exception/incident with triage controls in reach, not a generic list |
| A3.3 | Queue hygiene | Done items leave the queue on action without manual refresh |

### A4 — Dashboards (`/dashboards`, `/dashboards/:id`) — U2 U4 U5

| # | Test | Expectation |
|---|---|---|
| A4.1 | List page | Existing dashboards visible with meaningful metadata; create is obvious |
| A4.2 | Create + add widget | Builder is discoverable (palette of widget types); widget renders with real data immediately |
| A4.3 | Drag/resize | Smooth, persists on reload |
| A4.4 | Delete dashboard | Confirmation names the dashboard; escape/cancel works |
| A4.5 | "Set as landing page" | Takes effect next session start; discoverable in either dashboard or settings |

### A5 — Status (`/status`) — U1 U8 (P3's page)

| # | Test | Expectation |
|---|---|---|
| A5.1 | 10-second legibility | A non-user can state overall health, worst dataset, and trend without hovering |
| A5.2 | Numbers agree | Failing counts match Home, Checks, and Exceptions pages at the same moment |

### A6 — Data catalog (`/catalog`) — U1 U2 U4

| # | Test | Expectation |
|---|---|---|
| A6.1 | Browse curated datasets | Cards explain what each dataset is and what one click will do (creates connection + dataset + checks?) |
| A6.2 | One-click add | Progress feedback during setup; ends on the new dataset (or a clear success with link); failure explains why (note: #261 territory — dedupe, but score the *experience* of failure) |
| A6.3 | Re-add same dataset | Either idempotent or clearly blocked — no silent duplicates |

### A7 — Connections (`/connections`, `/:id`, `/:id/browse`) — U2 U4 U5

| # | Test | Expectation |
|---|---|---|
| A7.1 | List | Type, health, dataset count per connection; test-connection affordance |
| A7.2 | Create flow (open form, don't submit) | Driver-specific fields with examples; secrets masked; validation before submit |
| A7.3 | Detail page | Datasets under the connection; recent activity; edit is reachable |
| A7.4 | Browse tables | Schema tree loads with feedback; a table can be promoted to a monitored dataset in ≤2 clicks; already-monitored tables are marked |
| A7.5 | Delete connection | Confirm names blast radius (datasets/checks affected) |

### A8 — Datasets list (`/datasets`) — U1 U2 U3

| # | Test | Expectation |
|---|---|---|
| A8.1 | Scan the list | Health, check count, exception count, last run per row; sortable; filter by connection/health |
| A8.2 | Search within list | Substring match on name; instant |
| A8.3 | Row click + star | Row → detail; star → appears in sidebar Favorites immediately |

### A9 — Dataset detail (`/datasets/:id` + 12 tabs) — U1 U2 U3 U4 U6

The hub page. Tabs: profile, code, schema, lineage, contract, monitors, checks, runs,
exceptions, dashboards, knowledge, rca.

| # | Test | Expectation |
|---|---|---|
| A9.1 | Header | Name, connection, health, last-run recency; actions (run checks, star) at top; breadcrumb back |
| A9.2 | Tab persistence | URL reflects tab (`/datasets/:id/:tab`); reload lands on same tab |
| A9.3 | Profile tab | Column stats table + distributions; sample-vs-population honesty (#269 dedupe — but any "stats" labeled as full-table when sampled is a U8 finding) |
| A9.4 | Schema tab | Columns, types, nullability; schema-change history if any |
| A9.5 | Checks tab | Checks scoped to dataset; create-check entry point; enable/disable feedback |
| A9.6 | Runs tab | Scoped run history with status/duration; row → run detail |
| A9.7 | Exceptions tab | Scoped triage view consistent with global Exceptions (same controls — U6) |
| A9.8 | Lineage tab | Renders scoped graph; nodes navigate |
| A9.9 | Contract / Monitors / Knowledge / RCA / Code / Dashboards | Each tab either delivers content or a designed empty state with a call-to-action; **no tab may be a blank pane** |
| A9.10 | Cross-tab counts | Badge/counts on tabs (if any) match tab contents |

### A10 — Checks (`/checks`, `/checks/:id`) — U2 U4 U5 U6

| # | Test | Expectation |
|---|---|---|
| A10.1 | List | Filter by dataset/type/status; each row: dataset, type, last result, trend |
| A10.2 | Create check | Form explains each check type in analyst language; params validated; preview/dry-run if promised |
| A10.3 | Detail | Definition, run history chart, recent exceptions, edit/disable/delete; "run now" gives async feedback and lands somewhere useful when done |
| A10.4 | Disable check | Immediate visual state change; exceptions/history preserved (U5: no silent destruction) |
| A10.5 | Delete check | Confirm with blast radius |

### A11 — Runs (`/runs`, `/runs/:id`) — U3 U4 U8

| # | Test | Expectation |
|---|---|---|
| A11.1 | List | Status, dataset, check, duration, rows evaluated; filterable by status/dataset; failed runs visually loud |
| A11.2 | Run detail | What ran, verdict, metrics, link to produced exceptions, link back to check & dataset |
| A11.3 | Errored run | Error message legible to an analyst (not a raw traceback), with next-step guidance |

### A12 — Exceptions triage (`/exceptions`) — U2 U3 U4 U5 U7 — **the core loop, deepest pass**

| # | Test | Expectation |
|---|---|---|
| A12.1 | Default view | Open items first, newest first; volume visible; filters for dataset/check/status/assignee/recurrence prominent |
| A12.2 | Row expansion / detail | Offending row payload readable; check context and "why flagged" visible without leaving the queue |
| A12.3 | Single triage action | Status change/assign in ≤2 interactions from the row; optimistic UI or spinner; toast on completion |
| A12.4 | Bulk triage | Multi-select → bulk resolve/assign; count confirmation; queue updates without full reload |
| A12.5 | Triage note | Note survives and is visible in item history (dedupe #272 but verify the fixed UX end-to-end) |
| A12.6 | Filter persistence | Filters encode in URL; back/forward and share-link reproduce the exact view; pagination survives an action (no jump to page 1 after resolving one item) |
| A12.7 | Search | `q` search returns sane results (dedupe #273); punctuation in query doesn't explode |
| A12.8 | CSV export | Capped, labeled, CSV-injection-safe (#66 standard); button communicates row count |
| A12.9 | Keyboard pass | j/k-style or Tab navigation through the queue; action without mouse (this is the U7 flagship) |
| A12.10 | Deep link cold (P2) | `/exceptions?dataset_id=N` in fresh tab: filtered view with dataset context banner, not a bare table |

### A13 — Incidents (`/incidents`) — U1 U2 U4

| # | Test | Expectation |
|---|---|---|
| A13.1 | List | Groups related exceptions; severity, age, status; open → constituent exceptions |
| A13.2 | Lifecycle action | Ack/resolve visible with feedback; state consistent with exceptions underneath |

### A14 — Reliability (`/reliability`) — U1 U8 (P3's page)

| # | Test | Expectation |
|---|---|---|
| A14.1 | SLA/error-budget/MTTR panels | Each metric labeled with definition & window; no unexplained acronyms without tooltip |
| A14.2 | Trend charts | Axes labeled; zero/empty periods rendered honestly, not interpolated away |

### A15 — Workbench (`/workbench`) — U2 U4 U5

| # | Test | Expectation |
|---|---|---|
| A15.1 | Cold open | Purpose obvious: pick connection, write SQL, run; recent/saved queries visible |
| A15.2 | Run a SELECT | Results in a virtualized grid <5s; row count and truncation stated; long query cancellable or at least indicated |
| A15.3 | SQL error | Database error rendered readably at the right line if possible; query text preserved |
| A15.4 | Context arrival (`?exception_id=`) | Arriving from an exception pre-loads relevant scoped query/context |
| A15.5 | Save query | Named, appears in saved list and (per glossary) global search |

### A16 — Lineage (`/lineage`) — U1 U3 U4

| # | Test | Expectation |
|---|---|---|
| A16.1 | Graph render | Loads with real edges; health-colored nodes; legend present |
| A16.2 | Node interaction | Click → dataset detail or focus panel; zoom/pan smooth; fit-view control |
| A16.3 | Scoped arrival (`?connection=`) | Pre-filtered graph, filter visible and clearable |

### A17 — Assistant (`/assistant`) — U4 U8 — **LLM-off degradation flagship**

| # | Test | Expectation |
|---|---|---|
| A17.1 | Open with LLM disabled | Immediate, prominent "AI is not configured" state with pointer to Settings/docs — **not** an enabled-looking chat box |
| A17.2 | Attempt to send (if enabled-looking) | Instant clear failure, not a hang (this was #266's silent-off; verify the UX after remediation) |
| A17.3 | Other AI surfaces (check-gen, RCA, explain) | Each AI button app-wide is either hidden, disabled-with-tooltip, or fails instantly with explanation — consistent treatment (U6) |

### A18 — Settings (`/settings`) — U1 U5 U7

| # | Test | Expectation |
|---|---|---|
| A18.1 | Scan | Sections labeled; user management (admin), tokens, appearance, landing page discoverable |
| A18.2 | Save feedback | Every save gives confirmation; invalid input rejected inline |
| A18.3 | Theme toggle | Light/dark applies instantly app-wide; persists; charts/tables remain legible (→ feeds C2 sweep) |

### A19 — Global chrome — U1 U3 U7

| # | Test | Expectation |
|---|---|---|
| A19.1 | Command palette | Keyboard shortcut opens it (hint visible somewhere); typing a dataset name → grouped results (datasets/checks/connections/saved queries per Layout code); Enter navigates; recents shown on empty query |
| A19.2 | Sidebar | Active section highlighted; favorites live-update; health pill truthful (A2.5); collapse behavior if any |
| A19.3 | Breadcrumbs/back | Detail pages offer a path back that preserves list filters (no filter amnesia) |
| A19.4 | 404 / bad id | `/datasets/99999` → designed not-found with a way back, not a crash or infinite spinner |

### A20 — Docs & Features (public, `/docs`, `/features`) — U1 U8

| # | Test | Expectation |
|---|---|---|
| A20.1 | Reachability | Discoverable from the app (help affordance), not only by URL |
| A20.2 | Content honesty | Described features exist in the build; no vaporware claims |

---

## 4. Journey tests (cross-page, timed)

These simulate real sessions and are scored on U2/U3 primarily. Click counts are recorded.

| # | Journey | Script | Expectation |
|---|---|---|---|
| J1 | **Morning triage** (P1) | Login → assess health → open worst dataset → open its exceptions → resolve 2 + assign 1 with note → verify queue reflects it | ≤ 12 interactions to first completed triage; no state loss; every hop context-preserving |
| J2 | **Onboard a source** (P1/admin) | Catalog → add curated dataset → watch setup → view profile → generate/see checks → trigger run → see results | Feedback at every stage; ends with a runnable, visible check set; failure states explained |
| J3 | **Incident to cause** (P1) | Incidents → pick one → constituent exceptions → dataset RCA tab → explanation (LLM off ⇒ honest degradation) | Path exists without URL surgery; ≤ 8 interactions |
| J4 | **Cold deep link** (P2) | Fresh session → paste `/exceptions?dataset_id=N` → login → land | Post-login redirect preserves the deep link (A1.6); page self-sufficient for a stranger |
| J5 | **Find one thing fast** (any) | From any page: find check named X and open it via palette | ≤ 5s, ≤ 4 keystrokes + Enter beyond the name |

---

## 5. Cross-cutting sweeps

| # | Sweep | Method | Expectation |
|---|---|---|---|
| C1 | **Keyboard-only** | Repeat J1 without mouse | Completable; visible focus at all times; no traps |
| C2 | **Dark mode** | Toggle theme, revisit A2/A9/A12/A16 screenshots | AA contrast; charts/status colors legible; no unstyled white islands |
| C3 | **Narrow viewport** | 1280×800 and ~1024 width | No horizontal scroll of the app shell; tables degrade gracefully (the tool is desktop-first; phone is out of scope) |
| C4 | **Honest-time audit** | Grep every visible timestamp/window label across screens | UTC-honest, windowed labels; consistent format app-wide |
| C5 | **Empty-state audit** | Visit a dataset/tab with no data | Designed empties with next-step CTA everywhere (no blank panes) |
| C6 | **Error-state audit** | Bad ids, malformed queries, (if safe) brief API stop | Readable failures with recovery path; no white-screen; ErrorBoundary works |
| C7 | **Console hygiene** | Watch devtools console across the whole pass | No uncaught errors/warnings storms in normal navigation |

---

## 6. Deliverables

1. This plan (pre-registered expectations).
2. `ux-benchmark-report-2026-07.md` — per-area pillar scores (1–5), overall scorecard,
   severity-ranked findings (P0–P3) each with reproduction + screenshot reference,
   explicit dedupe notes vs #256–275, and top-N recommendations ranked by
   analyst-time saved.
3. Screenshot evidence set.
