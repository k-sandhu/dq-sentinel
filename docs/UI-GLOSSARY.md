# DQ Sentinel — UI Glossary, Click Map & Gap Analysis

> **What this is.** A screen-by-screen inventory of every clickable element in the
> frontend, *where each click takes you*, and a verdict on whether that wiring helps or
> hurts. The goal is to expose the **navigation flow** and the **dead-ends / missing edges**
> that quietly tax usability.
>
> Last reviewed: 2026-06-19. Derived from a full read of `frontend/src/` (App routes,
> `Layout`, every page, the 12 dataset tabs, and the shared table / triage / lineage /
> dashboard components). Verdict legend:
>
> | Mark | Meaning |
> |---|---|
> | ✅ | Good — does the expected thing, or a genuinely smart flow |
> | ⚠️ | Minor friction — works, but surprises or under-delivers |
> | ❌ | Gap — dead-end, missing link, or inconsistent with the rest of the app |
> | 🔵 | Intentionally inert (display only / no navigation) |
>
> This is the single UI glossary — it consolidates the former `ui-flow-glossary.md`. A
> companion review, [`BROKEN-FLOWS.md`](BROKEN-FLOWS.md), tracks the *broken journeys* (and
> what's been fixed) at a higher altitude; this file is the exhaustive element map.

---

## 0. Route map (the skeleton)

```
/login                         LoginPage              (unauthenticated)
/                              HomePage               "Data quality scorecard" (landing is configurable, #59)
/my-work                       MyWorkPage             daily operating console
/dashboards                    DashboardsListPage
/dashboards/:id                CustomDashboardPage    drag-and-drop widget builder
/connections                   ConnectionsPage
/connections/:id               ConnectionDetailPage
/connections/:id/browse        ConnectionBrowsePage   "Browse tables"
/datasets                      DatasetsPage
/datasets/:id[/:tab]           DatasetDetailPage      tabs: profile|code|schema|lineage|contract|
                                                            monitors|checks|runs|exceptions|
                                                            dashboards|knowledge|rca
/checks                        ChecksPage
/checks/:id                    CheckDetailPage
/runs                          RunsPage
/runs/:id                      RunDetailPage
/exceptions                    ExceptionsPage         accepts ?dataset_id ?run_id ?check_id ?assignee ?recurrence …
/incidents                     IncidentsPage
/reliability                   ReliabilityPage        SLA / error-budget / MTTR
/workbench                     WorkbenchPage          accepts ?dataset_id ?run_id ?exception_id ?check_id ?connection_id
/lineage                       LineagePage            accepts ?connection
/assistant                     AssistantPage
/settings                      SettingsPage
/docs, /docs/:slug             DocsPage               standalone reference (no app shell)
/features                      FeaturesPage           standalone reference (no app shell)
*                              → redirect to /
```

On the first visit to `/` per browser session, a `LandingRedirect` (#59) sends the user to
their configured landing page (Settings / dashboard "Set as my landing page"); deep links
with a query or non-`/` path always bypass it.

Two roles gate interaction throughout: **`canEdit`** (editor/admin) unlocks mutate +
SQL-run controls; **`isAdmin`** unlocks connections and user management. Viewers see a
read-only app with controls hidden or disabled.

---

## 1. Global chrome (present on every authenticated screen)

`Layout.tsx` wraps every route except `/login`, `/docs`, and `/features`. It is the user's
permanent compass, so its wiring matters more than any single page.

### Sidebar

| Element | Click target | Verdict |
|---|---|---|
| **DQ Sentinel** logo (top) | `<Link to="/">` (`aria-label="DQ Sentinel — home"`) | ✅ Logo → home, the universal convention. (Was a dead `<div>` at the last review.) |
| Overview › **Home** | `/` | ✅ |
| Overview › **My work** | `/my-work` | ✅ The analyst's daily queue. |
| Overview › **Dashboards** | `/dashboards` | ✅ |
| **Favorites** (dynamic group) | starred datasets → `/datasets/:id` | ✅ Appears only when you've starred datasets; capped. |
| Sources › **Connections** | `/connections` | ✅ |
| Sources › **Datasets** | `/datasets` | ✅ |
| Quality › **Checks** | `/checks` | ✅ |
| Quality › **Runs** | `/runs` | ✅ |
| Quality › **Exceptions** | `/exceptions` | ✅ |
| Quality › **Incidents** | `/incidents` | ✅ |
| Quality › **Reliability** | `/reliability` | ✅ |
| Explore › **Workbench** | `/workbench` | ✅ |
| Explore › **Lineage** | `/lineage` | ✅ |
| Explore › **Assistant** | `/assistant` | ✅ |
| **Settings** (footer) | `/settings` | ✅ Sensibly separated from the task nav. |
| User name / email block | *nothing* | 🔵 Display only. Still where users look for "Profile / change password" — there is no such screen. |
| **Sign out** | `logout()` → hard `window.location.href="/login"` | ✅ Full reload is the correct way to dump auth state. |

> The nav grouping (Overview / Sources / Quality / Explore) is genuinely good information
> architecture — it mirrors the mental model of the product. ✅

### Topbar

| Element | Click target | Verdict |
|---|---|---|
| **Fleet-health pill** ("Sources healthy" / "N failing" / "No sources") | `navigate("/connections")` | ✅ Polls `/connections/health` every 60s and **shares the `fleet-health` cache key** with ConnectionsPage, so the per-row status badges there are warm on arrival. |
| **Global search** input | debounced `GET /search?q=`; grouped results | ✅ Now searches **datasets, checks, connections, and saved queries** (was datasets-only). `/` or ⌘/Ctrl-K focuses it; ↑/↓ move a highlight; Enter opens it; empty-focus shows "Recently viewed." |
| Search result row | `navigate(...)` to the hit's detail | ✅ |
| **Theme toggle** (moon/sun) | flips `html[data-theme]`, persists to `localStorage` (`dq-theme`) | ✅ |
| **Density toggle** | toggles compact/comfortable, persists `dq-density` | ✅ New affordance for dense tables. |
| **Docs launcher** (floating ?) | menu → `/docs`, `/features` | ✅ Pinned bottom-right on every in-app page; the two targets are standalone, shell-less reference pages. |

---

## 2. Login (`/login`)

| Element | Click target | Verdict |
|---|---|---|
| Email / Password fields | — | 🔵 `autoFocus` on email. ✅ |
| **Sign in** | `login()` → app re-renders into the landing route | ✅ |
| (Dev hint text) | — | 🔵 Shows seeded creds (`admin@example.com / admin123`) — fine for dev, **must not ship to prod**. ⚠️ |

No "forgot password", no "show password", no "request access". Acceptable for an internal
tool; note it.

---

## 3. Home — "Data quality scorecard" (`/`)

Rebuilt since the last review from a card-launchpad into an **executive scorecard**: a
headline score band, operational metrics, a run-trend chart, "score drivers", rollups, and a
recent-runs table. The wasted-click problem the old KPI cards had is largely solved — the
metric tiles and the trend now deep-link.

| Element | Click target | Verdict |
|---|---|---|
| **Add data** (header) | `/connections` | ⚠️ Still lands on the Connections list, not an "add" form. |
| Scorecard **metric tiles** (Datasets, Active checks, Failing checks, Open exceptions, Runs 24h, 7-day pass rate) | `StatCard`/`ScoreMetric` render as `<Link>` → `/datasets`, `/checks?status=active`, `/checks?status=active&last_status=fail&last_status=error`, `/exceptions`, `/runs?since=24h`, `/runs?since=7d` | ✅ The headline numbers are now buttons — the biggest wasted-click surface from last review is wired. |
| Run-trend **chart bars** | `onClick` → `/runs?day=<date>&status=<status>` | ✅ A day's stacked bar now drills into that day's runs by status. (Was inert dataviz.) |
| **Score drivers** rows | dataset driver → `/datasets/:id/exceptions`; rollup driver → `/datasets?…` filter | ✅ Replaces the old "needs attention" card; still deep-links failing datasets straight to their exceptions. |
| "Run results" → **Runs** | `/runs` | ✅ |
| Recent-runs rows | (see §12, RunsTable) | — |

**Still missing:** a first-run onboarding checklist (Connect → Browse → Register → Profile →
Generate). The spine exists but isn't guided on Home.

---

## 4. My work (`/my-work`)

The analyst's "9am queue" (#64). Loads `GET /dashboard/console`.

| Element | Click target | Verdict |
|---|---|---|
| Stat cards (**Assigned to me / New·24h / Regressed / Open total**) | `/exceptions?assignee=me&status=open`, `?recurrence=new&status=open`, `?recurrence=recurring&status=open`, `?status=open` | ✅ Every count is a filtered deep-link into triage. |
| "Failing now" rows | check → `/checks/:id`; "View exceptions →" → `/exceptions?check_id=:id&status=open` | ✅ |
| "Biggest movers" rows | `/datasets/:id/exceptions` | ✅ |
| Header "All exceptions" | `/exceptions` | ✅ |

A genuinely strong cold-start surface — it answers "what do I do first" with live links.

---

## 5. Connections (`/connections`)

| Element | Click target | Verdict |
|---|---|---|
| **Check fleet health** | `health.refetch()` — probes every source, fills Status column | ✅ On-demand by design. |
| **Add connection** (admin) | opens `AddConnectionModal` | ✅ |
| ↳ Engine chip (SQLite, Postgres…) | toggles a DSN template into the field; dot shows driver-installed | ✅ Edit a template instead of typing a DSN cold. |
| ↳ **Test connection** | `POST /connections/test` | ✅ Validate before commit. |
| ↳ **Save** | `POST /connections` → invalidates list, closes | ✅ |
| Connection **row** (as a whole) | `navigate("/connections/:id")` | ✅ Whole-row click now matches Datasets/Checks/Runs. |
| Connection **name** | `/connections/:id` (`row-title-link`) | ✅ |
| **Browse tables** (per row) | `/connections/:id/browse` (stops propagation) | ✅ |
| **Delete** (admin) | styled `useConfirm()` dialog, **`typeToConfirm` = connection name**, cascade spelled out | ✅ The native `confirm()` is gone; the highly-destructive cascade now requires typing the name. |

---

## 6. Connection detail (`/connections/:id`)

New since last review — a connection is no longer a second-class object.

| Element | Click target | Verdict |
|---|---|---|
| Breadcrumb **Connections** | `/connections` | ✅ |
| Stat cards (Datasets / Active checks / Open exceptions / Latest health) | *display* | 🔵 |
| **Test connection** | `POST /connections/:id/test` → appends to an on-page health timeline | ✅ |
| **Browse tables** | `/connections/:id/browse` | ✅ |
| Datasets table **row** | `/datasets/:id` | ✅ |

**Gaps:** ❌ Still no **rename** and no **edit-DSN** — changing a DSN means delete + re-add.
The DSN is masked with no copy/reveal.

---

## 7. Browse tables (`/connections/:id/browse`)

| Element | Click target | Verdict |
|---|---|---|
| **← Connections** | `/connections` | ✅ |
| Filter box | local filter (table name only) | ⚠️ Ignores **schema** and kind. |
| Table **row** (unregistered) | toggles selection (whole row + checkbox) | ✅ Row stops propagation correctly. |
| **already registered →** | `/datasets/:id` | ✅ |
| **Register N datasets** (editor) | `POST /datasets/register` → 1 result: `/datasets/:id`; many: `/datasets` | ✅ Single registration jumps into the new dataset; bulk drops you on the list. |

**Gaps:** ⚠️ After registering, the new dataset is **not auto-profiled** (no "Register &
profile" hand-off); no **"select all visible."**

---

## 8. Datasets (`/datasets`)

| Element | Click target | Verdict |
|---|---|---|
| **Browse sources** (header) | `/connections` | ✅ |
| Search box | local filter (table / connection / owner) | ✅ |
| Health filter chips (all/fail/warn/pass/unknown) | local filter | ✅ |
| **★ favorite** (per row) | toggles a starred dataset (surfaces in the sidebar Favorites group) | ✅ |
| Dataset **row** | `navigate("/datasets/:id")` → Profile tab | ✅ Whole-row click. |

**Gaps:** ⚠️ Sort is fixed (favorites first, then most open-exceptions) — not user-sortable;
rows still offer **no shortcut** to a dataset's Checks / Runs / Exceptions; the **connection
cell isn't a link**.

---

## 9. Dataset detail (`/datasets/:id/:tab`) — the workspace

Where users spend their time: a header + **12 tabs**.

### Header

| Element | Click target | Verdict |
|---|---|---|
| **Breadcrumb** `Datasets › schema.table` | "Datasets" → `/datasets` | ✅ The pervasive wayfinding hole from last review is fixed. |
| Sub-line "N active checks · M open exceptions" | *display* | 🔵 The counts that should also badge the tab strip live here. |
| **★ favorite** | toggles star | ✅ |
| **Workbench** | `/workbench?dataset_id=:id` | ✅ Carries dataset context → Workbench preloads suggestions. |
| **Profile now** (editor) | `POST /datasets/:id/profile` | ✅ |
| Title `schema.table` + health Pill | *nothing* | 🔵 Inert (minor). |
| Tab buttons | `navigate("/datasets/:id/:tab")` — URL-routed, deep-linkable | ✅ |

> **Cross-tab gap (persists):** the tab strip still shows **no per-tab badges** — no
> "3 proposed", no "RCA running". The header sub-line carries two counts, but you still open
> each tab to discover work inside it. ⚠️

### 9a. Profile tab

| Element | Click target | Verdict |
|---|---|---|
| **Preview rows** / Hide | toggles a 25-row sample (lazy `GET /preview`) | ✅ |
| **Profile this dataset** (empty state, editor) | runs profiling | ✅ |
| AI "Exploration insights" cards | *nothing* | 🔵 Read-only narrative. |
| Per-column stat cards + PK/temporal badges | *nothing* | ⚠️ Still pure display — the natural launch point for "add a check on this column" or "chart it." |

### 9b. Code tab

| Element | Click target | Verdict |
|---|---|---|
| **Copy** | `navigator.clipboard.writeText(ddl)` → "Copied" | ✅ |
| **Pinned queries** card → "Open in workbench →" | `/workbench?...` per saved query | ✅ Saved queries pinned to this dataset surface here. |

**Gap:** ⚠️ Long DDL has no wrap/search; the DDL card itself has no "Open in Workbench."

### 9c. Schema tab `(new)`

Schema **history & baseline** (#101) — distinct from Code (raw DDL).

| Element | Click target | Verdict |
|---|---|---|
| Snapshot timeline (left) | selects a snapshot; change chips (+added / −removed / retyped / nullability / reorder) | ✅ Snapshots are captured on profiling and on `schema_change` runs. |
| **Pin current as baseline** | `POST /datasets/:id/schema-baseline` | ✅ Sets what `baseline=pinned` checks enforce. |

### 9d. Lineage tab

| Element | Click target | Verdict |
|---|---|---|
| Depth chips **1 / 2 / 3** | re-query at that BFS depth | ✅ |
| **Open estate lineage** | `/lineage?connection=:connId` | ✅ |
| Graph **node** (registered) | standardized via `lib/lineageNav.ts`: fail/warn → `/datasets/:id/exceptions`, else `/datasets/:id` | ✅ One default destination per node, consistent with the estate view. |
| Graph node (external / unregistered) | inert; detail panel explains "register it as a dataset to open it" | ⚠️ Better than the old silent node, but still **no one-click register**. |

### 9e. Contract tab `(new)`

**Data contracts** (#105) — author / version / enforce.

| Element | Click target | Verdict |
|---|---|---|
| Empty state **Create draft** / **Import ODCS YAML** | seeds a spec from profile+knowledge / parses ODCS v3 | ✅ |
| Mode chips **Editor / ODCS YAML / Versions** | switch view | ✅ |
| Editor (Agreement / Schema / Freshness & Volume / Quality clauses) | edits the spec | ✅ "Add source columns" pulls live columns. |
| **Activate** | `POST …/contract/activate` → materializes clauses into real scheduled **checks** + pins a schema baseline | ✅ This is the enforcement bridge — reports "N checks created, M updated." |
| **Conformance** panel | live per-clause pass/breached/neutral (polls 30s); links materialized clauses to their Check | ✅ |

### 9f. Monitors tab `(new)`

A managed **monitor pack** — a reconcilable bundle of auto-managed Freshness / Volume /
Schema / Drift checks.

| Element | Click target | Verdict |
|---|---|---|
| **Reconcile now** (editor) | `POST …/monitor-pack/reconcile` — creates/refreshes the monitors | ✅ |
| **Enable / Disable pack**, **Configure** | `PATCH …/monitor-pack` (per-kind on/off, cadence, sensitivity) | ✅ Requires a profile first. |
| Per-kind check rows | link out to the Checks tab | ✅ |

### 9g. Checks tab

| Element | Click target | Verdict |
|---|---|---|
| **Generate checks (AI/heuristic)** (editor) | `POST /checks/generate`; disabled until profiled (tooltip says why) | ✅ |
| **Explore data first** checkbox | toggles the agentic explorer | ✅ |
| **New check** (editor) | opens `NewCheckModal` → **schema-driven `CheckParamsForm`** | ✅ Per-type fields, inline validation, live "Effective params" preview; raw JSON behind an **Advanced** toggle. (Was a raw-JSON textarea.) |
| **Proposed checks** → Activate / Edit / **Dismiss** | mutate / edit modal / **`useConfirm()`** | ✅ Dismiss now confirms. |
| Active checks → Run / Pause / Resume / Edit / **Archive** | per-row mutate / **`useConfirm()`** | ✅ Archive now confirms. |
| Check **row** / name | `/checks/:id` | ✅ Opens the check detail (shared `ChecksTable`). |

### 9h. Runs tab → see RunsTable (§12). 9i. Exceptions tab → see Exceptions workspace (§13).

### 9j. Dashboards tab

| Element | Click target | Verdict |
|---|---|---|
| Focus input + **Generate dashboard** (editor) | `POST /adhoc-dashboards/generate`, then auto-opens it | ✅ |
| Saved dashboard (left list) | loads + re-runs panels live | ✅ |
| Panel **Show SQL** | toggles the panel's SQL | ✅ |
| **Delete** (editor) | `useConfirm()` → `DELETE /adhoc-dashboards/:id` | ✅ Now confirmed. |

**Gap:** ⚠️ Existing dashboards aren't **auto-selected** → the right pane is empty until you
click one.

### 9k. Knowledge tab

| Element | Click target | Verdict |
|---|---|---|
| Form fields (context, issues, importance, owner, SLA, PII, notes) | local form; `fieldset disabled` for viewers | ✅ |
| **Save knowledge** (editor) | `PUT /datasets/:id/knowledge` | ✅ |
| (tab-away with unsaved edits) | dirty-state guard → confirm before leaving (+ `beforeunload`) | ✅ The silent-loss bug from last review is fixed. |

**Gap:** ❌ **PII columns are free text**, not validated against the dataset's real columns —
a redaction-correctness risk, since these names drive prompt redaction.

### 9l. RCA / Root cause tab

| Element | Click target | Verdict |
|---|---|---|
| Question input + **Investigate** (editor+LLM) | `POST /rca/start` → polls every 4s while running | ✅ |
| **Investigation transcript** (`<details>`) | expands the agent's SQL trace | ✅ Evidence is inspectable. |

**Gap:** ❌ A running RCA has **no stop/cancel** (the Assistant does — an inconsistency). *(The
tab's tip now correctly points to a run's **detail page** — open a failed run and choose
"Start RCA" — instead of implying the Runs-table row launches RCA.)*

---

## 10. Checks (`/checks`) — global, + Check detail (`/checks/:id`)

| Element | Click target | Verdict |
|---|---|---|
| Search box | local filter (name/column/type/dataset) | ✅ |
| Status chips (all/active/proposed/disabled) | refetch by status | ✅ |
| Check **row** / name | `/checks/:id` | ✅ Opens the check detail (was a dead row). |
| Dataset name (per row) | `/datasets/:id/checks` | ✅ |
| Run / Pause / Edit / Archive (editor) | per-row mutate (Archive confirms) | ✅ |

**Check detail (`/checks/:id`)** shows config + a **violation-trend chart** + **run history**
(`RunsTable`), with header actions **Run now** (→ the new run), **Workbench**, and **Dataset
checks**. A check is now a real, linkable object.

**Gap:** ❌ Still **no "New check"** button on the global page — creation lives only inside a
dataset's Checks tab. A user who clicks "Checks" intending to *add* one hits a wall.

---

## 11. Runs (`/runs`) — global, + Run detail (`/runs/:id`)

| Element | Click target | Verdict |
|---|---|---|
| Status chips (all/fail/warn/error/pass) | refetch | ✅ Auto-refreshes. |
| **Root-cause latest failure** (editor+LLM, only if failures exist) | `POST /rca/start` on the *newest* failure → `/datasets/:id/rca` | ⚠️ Only ever the single newest failure; hidden for viewers / LLM-off. |
| RunsTable rows | see §12 | — |

**Run detail (`/runs/:id`)** shows metrics, run details, the violation query (custom SQL, or
a note that built-in compilers don't persist SQL), and the captured exceptions; header
actions **Check** (→ `/checks/:id`), **Open in Workbench**, **Triage exceptions**, and a
per-run **Start RCA** (enabled for fail/warn/error with LLM on). The "a run isn't a place you
can stand on" gap is fixed.

---

## 12. RunsTable (shared: Home, Runs, dataset Runs tab, Check detail)

| Element | Click target | Verdict |
|---|---|---|
| Run **row** (as a whole) | `/runs/:id` | ✅ Opens run detail (success rows included). |
| **Check name** | `/runs/:id` | ⚠️ Links to the *run*, not `/checks/:id` — from a run you still can't jump to the check now that a check page exists. |
| Dataset name | `/datasets/:id` | ✅ |
| **N exceptions** | `/exceptions?run_id=:id` | ✅ |
| **Investigate in workbench →** (fail/warn/error rows) | `/workbench?dataset_id=:id&run_id=:id` | ✅ Carries run context → Workbench auto-suggests investigation SQL. |

---

## 13. Exceptions workspace (shared: Exceptions page + dataset Exceptions tab)

`/exceptions` → `ExceptionsPage` → `ExceptionsTriage` (now a thin shim) → the real
**`ExceptionsWorkspace`** (saved views, filter bar, table, detail panel, keyboard triage).

| Element | Click target | Verdict |
|---|---|---|
| Header **dataset `<select>`** (Exceptions page) | `patchParams` clones existing params, sets `dataset_id` | ✅ **Preserves `run_id`** (and other filters) — the silent run-scope wipe is fixed; `run_id` also shows as a removable chip. |
| **Saved views** (My open / New today / High severity / Recurring / Unassigned / Expected, + user-saved) | apply a filter set (counts shown) | ✅ User views persist to `localStorage`; each has a × to delete. |
| **Filter bar** (status, severity — chip multi-selects with counts; check type, recurrence, assignee, time-last-seen, sort, grouping, search) | refetch | ✅ Far richer than the old status-only chips. "Clear all" resets. |
| Select-all / per-row checkbox | selection (stops row-click propagation) | ✅ |
| Bulk bar: Acknowledge / Mark expected / Resolve / Mute / Reopen / **Assign to me** | `POST /exceptions/triage` (+ optional note) | ✅ |
| Exception **row** | opens the **detail panel** (side panel, replaced the modal) | ✅ |
| ↳ Panel **check name** | `/datasets/:id/checks` | ⚠️ Links to the dataset's Checks tab, not `/checks/:id`. |
| ↳ Panel **Run #N** | `/runs/:id` | ✅ |
| ↳ Panel **Investigate in workbench** / **RCA** | `/workbench?...` / RCA | ✅ |
| **Keyboard triage** | `j/k` move, `x` select, `o`/Enter open, `a/e/r/m/u` set status, `Shift+A` assign-me, `?` cheat-sheet (`ShortcutsHelp`) | ✅ Real power-user triage. |

---

## 14. Workbench (`/workbench`)

Accepts `?dataset_id ?run_id ?exception_id ?check_id ?connection_id` and turns them into
context-aware query **suggestions** — the connective tissue that makes "investigate →" pay off.

| Element | Click target | Verdict |
|---|---|---|
| Connection `<select>` | switches active source; **clears stale SQL/results** | ✅ The cross-source confusion from last review is fixed. |
| Schema table **+** | inserts a **schema-qualified, dialect-quoted** ref (or seeds `SELECT * FROM <qualified> LIMIT 50`) | ✅ Identifier quoting via `lib/sqlIdent.ts` — reserved words / mixed-case / cross-schema now work. |
| Column row | inserts a quoted column name | ✅ Same quoting. |
| **Run** (editor) | `POST /query/run` (Ctrl/Cmd+Enter) | ✅ |
| limit `<select>` / **Chart / Table** toggle / chart selects | local | ✅ |
| **History (N)** | `HistoryModal` — past runs (localStorage), Edit/Run, Clear | ✅ New: per-browser query history. |
| **Save** → `SaveQueryModal`; **SavedQueriesRail** | server `/queries` CRUD (name/tags/pin-to-dataset); load/run/delete | ✅ New: a shared saved-query library (pinned queries surface on a dataset's Code tab). |
| Suggestion **Run** / **Edit** | sets SQL (+runs) | ✅ For viewers, "Run" is hidden but "Edit" loads SQL into a disabled-run editor — a polite dead-end. ⚠️ |

**Gap:** ⚠️ No **"create a check from this query"** — a natural bridge that's still missing.

---

## 15. Lineage (`/lineage`) — estate-wide

Polished into a full **Lineage Explorer** (#151): pan/zoom graph, side panel, search,
health/schema/focus filters, fullscreen.

| Element | Click target | Verdict |
|---|---|---|
| Connection `<select>` | writes `?connection=` (shareable) | ✅ |
| **Search** box | jump/center/highlight matching nodes (Enter or "Jump") | ✅ New — graph search. |
| Graph **node** (registered) | `lib/lineageNav.ts`: fail/warn → `/datasets/:id/exceptions`, else `/datasets/:id` | ✅ Same standardized destination as the dataset Lineage tab — the old three-destinations inconsistency is gone. |
| Side panel secondary actions | explicit **Lineage / Exceptions / RCA / Workbench** links per registered node | ✅ |
| Graph node (external) | inert; panel explains it must be registered | ⚠️ Still no one-click "register this table." |

---

## 16. Assistant (`/assistant`)

| Element | Click target | Verdict |
|---|---|---|
| **New conversation** (editor) | `POST /chat/sessions` | ✅ |
| Session list item | loads thread over WebSocket | ✅ |
| Session **×** (delete) | `useConfirm()` → `DELETE /chat/sessions/:id` | ✅ Now confirmed. |
| Suggestion chips | `startWith()` — creates a session if needed, then sends | ✅ Great cold-start. |
| Composer | `Enter` sends, `Shift+Enter` newline | ✅ |
| **Send / Stop** / **Reconnect** | send / `socket.stop()` / `socket.reconnect()` | ✅ Mid-stream stop + explicit WS-failure recovery. |

The page is editor-gated; **viewers** land on a fully-disabled screen with info boxes. ⚠️

---

## 17. Incidents (`/incidents`)

The failure-lifecycle / escalation queue (incidents are a distinct, deduped object — not raw
exceptions).

| Element | Click target | Verdict |
|---|---|---|
| Filters (status / severity / dataset + text) | `GET /incidents` (URL-synced, 25/page) | ✅ |
| Incident **row** / title | opens `IncidentDetailModal` (`GET /incidents/:id`) — stats, context, external refs, timeline | ✅ |
| Per-row links **Dataset / Check / Run / Exceptions** | `/datasets/:id`, `/checks/:id`, `/runs/:id`, `/exceptions?dataset_id&run_id` | ✅ Dense but correct cross-links. |
| **Ack / Resolve** (editor) | `POST /incidents/:id/ack` \| `…/resolve` | ✅ |
| External ref | outbound `<a target="_blank">` when a URL is present | ✅ |

---

## 18. Reliability (`/reliability`)

SLA attainment / error-budget / MTTR dashboard (#102). Loads `GET /sla/reliability`.

| Element | Click target | Verdict |
|---|---|---|
| Header chips (total / breached) | *display* | 🔵 |
| **New SLA** (editor) | `NewSlaForm` → `POST /sla` (dataset scope, target type, objective %, 7d/30d window) | ✅ |
| **SLA card** (attainment %, good/bad, budget consumed, MTTR) | "Show trend" expands an SVG sparkline (`GET /sla/:id`) | ✅ |
| **Re-evaluate** / **Delete** (editor) | `POST /sla/:id/evaluate` / `DELETE /sla/:id` (confirm) | ✅ |

---

## 19. Dashboards (`/dashboards`, `/dashboards/:id`)

| Element | Click target | Verdict |
|---|---|---|
| Dashboard **card** | `/dashboards/:id` | ✅ Shows name, visibility (team/private), owner, widget count, updated-ago. |
| **New dashboard** | `POST /dashboards/custom` → `/dashboards/:id?edit=1` (builder) | ✅ |
| Builder (add / configure / reorder / span / remove widgets) | full-layout `PATCH` with a dirty guard | ✅ Widget types: metric, exceptions, checks, status_matrix, trend, sql (editor-only), note. |
| **Duplicate** / **Set as my landing page** / **Delete** | `POST …/duplicate` / `setLanding` (localStorage) / modal → `DELETE` | ✅ |

---

## 20. Settings (`/settings`)

| Element | Click target | Verdict |
|---|---|---|
| Health / LLM / "Signed in as" stat cards | *nothing* | 🔵 |
| **MCP › Add server** (admin) | modal → `POST /mcp-servers` | ⚠️ No "test connection." |
| MCP **Enable/Disable** | `PATCH` toggle | ✅ |
| MCP **Delete** (admin) | `useConfirm()` → `DELETE` | ✅ Now confirmed. |
| **Invite user** (admin) | modal → `POST /auth/users` | ⚠️ Sets a **raw password** — no invite/reset-email flow. |
| User **role `<select>`** | confirm dialog, then `PATCH` (snaps back on cancel) | ✅ No longer an unconfirmed mutate-on-change. (Still an inline select rather than a "Save role" button.) Own row disabled. |
| **Deactivate** user | `useConfirm()` (danger) → `PATCH` | ✅ Confirms (Activate doesn't — reversible). |

---

## 21. Standalone reference (`/docs`, `/features`)

Rendered **outside** the app shell (no sidebar), reached via the floating Docs launcher.
`DocsPage` renders repo markdown (`GET /docs`, `/docs/:slug`); `FeaturesPage` renders a static
feature catalog stamped with version + AI status from `GET /health`. Both wrap an
`InfoShell`. 🔵 Reference surfaces — fine.

---

## 22. The intended flow (and whether you can click it end-to-end)

```
Home / My work ──▶ Connections ──Add connection──▶ (modal: Engine→Test→Save)
   │                    │
   │                    └──Browse tables──▶ Browse ──Register N──▶ Dataset (Profile tab)
   │                                                                   │
   │                                       Profile now ◀───────────────┘
   │                                                                   │
   │                         Checks / Contract / Monitors ──Generate / New / Activate──▶ active checks
   │                                                                   │
   │                          (worker runs on schedule) ──▶ Runs ──▶ failures ──▶ Incidents
   │                                                                   │
   │                              N exceptions ◀──────────────────────┤
   │                                   │                               │
   │                              Triage (saved views, keyboard)       │
   │                                                                   │
   │                              investigate → Workbench  /  RCA agent (Root cause tab / run detail)
   │
   └── Knowledge feeds every AI step · Reliability tracks SLAs · Lineage / Assistant are lateral tools
```

The **happy path is clickable end-to-end**, and the return trips that were weak a review ago
(breadcrumbs, run/check/connection detail, consistent row-click) are now wired. Remaining
weaknesses are smaller: a few dead display surfaces and the global "New check" hole.

---

## 23. Gaps, prioritized

### P1 — first-class objects, last mile
1. **No global "New check."** `/checks` can filter and drill in (rows now open `/checks/:id`),
   but can't *create* — creation lives only inside a dataset's Checks tab. Add "New check"
   with a dataset picker.
2. **Check references from runs/exceptions don't reach `/checks/:id`.** `RunsTable`'s check
   name links to the run; the exception panel's check name links to the dataset Checks tab.
   Point both at the check detail now that it exists.

### P2 — dead high-intent surfaces & missing next-step
3. **Profile column cards + PK/temporal badges** are display-only → wire "create check / chart
   this column."
4. **Dataset rows** have no shortcut to Checks / Runs / Exceptions; the **connection cell**
   isn't a link.
5. **Tab strip carries no per-tab badges** (proposed-count, open-exceptions, RCA-running) —
   the counts already exist in the header sub-line.
6. **Dashboards tab** doesn't auto-select an existing dashboard.
7. **RCA** has no stop/cancel control on a running session.
8. **Lineage external nodes** explain themselves but offer no one-click "register as dataset."
9. **No auto-profile** after registering; offer "Register & profile."

### P3 — account, layout, polish
10. **Login**: no forgot/reset-password, no show-password; **dev creds printed on screen**
    (must not ship).
11. **Settings**: MCP "Add server" has **no test connection**; **Invite sets a raw password**
    (no invite/reset email).
12. **Connection detail**: no rename / edit-DSN.
13. **Browse**: filter ignores schema; no "select all visible." **Code tab**: long DDL has no
    wrap/search.

---

## 24. What's genuinely good (keep these)

- **URL-routed dataset tabs** and **URL-driven filters** (exceptions, lineage, runs) — every
  meaningful view is deep-linkable and shareable.
- **Context-passing deep links**: run / exception → Workbench preloads tailored suggestions;
  failed run → RCA on the right dataset; "needs attention" → the exceptions tab specifically.
- **Dedicated detail pages** for runs, checks, and connections — failures and rules are now
  durable, linkable objects.
- **Breadcrumb + clickable logo + consistent whole-row navigation** — the wayfinding holes are
  filled.
- **Schema-driven check forms**; **saved views + keyboard triage**; **data contracts**,
  **SLA/reliability**, and **incident** surfaces — commercial-grade workflow.
- **Shared `confirm()` dialog** on every destructive action (typed confirmation for the
  cascading connection delete).
- **SQL transparency everywhere** (DDL, panel SQL, RCA transcripts, contract conformance);
  sensible role gating; sensible empty states with a CTA to the next step.
