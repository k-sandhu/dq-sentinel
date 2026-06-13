# DQ Sentinel — UI Glossary, Click Map & Gap Analysis

> **What this is.** A screen-by-screen inventory of every clickable element in the
> frontend, *where each click takes you*, and a verdict on whether that wiring helps or
> hurts. The goal is to expose the **navigation flow** and the **dead-ends / missing edges**
> that quietly tax usability.
>
> Derived from a full read of `frontend/src/` (App routes, `Layout`, all 13 pages, the
> 9 dataset tabs, and the shared table/triage/lineage components). Verdict legend:
>
> | Mark | Meaning |
> |---|---|
> | ✅ | Good — does the expected thing, or a genuinely smart flow |
> | ⚠️ | Minor friction — works, but surprises or under-delivers |
> | ❌ | Gap — dead-end, missing link, or inconsistent with the rest of the app |
> | 🔵 | Intentionally inert (display only / no navigation) |

---

## 0. Route map (the skeleton)

```
/login                         LoginPage              (unauthenticated)
/                              HomePage               "Data quality overview"
/connections                   ConnectionsPage
/connections/:id/browse        ConnectionBrowsePage   "Browse tables"
/datasets                      DatasetsPage
/datasets/:id[/:tab]           DatasetDetailPage      tabs: profile|code|lineage|checks|
                                                            runs|exceptions|dashboards|
                                                            knowledge|rca
/checks                        ChecksPage
/runs                          RunsPage
/exceptions                    ExceptionsPage
/workbench                     WorkbenchPage          accepts ?dataset_id ?run_id
                                                              ?exception_id ?check_id ?connection_id
/lineage                       LineagePage            accepts ?connection
/assistant                     AssistantPage
/settings                      SettingsPage
*                              → redirect to /
```

Two roles gate interaction throughout: **`canEdit`** (editor/admin) unlocks mutate +
SQL-run controls; **`isAdmin`** unlocks connections and user management. Viewers see a
read-only app with controls hidden or disabled.

---

## 1. Global chrome (present on every authenticated screen)

`Layout.tsx` wraps every route except `/login`. It is the user's permanent compass, so
its wiring matters more than any single page.

### Sidebar

| Element | Click target | Verdict |
|---|---|---|
| **DQ Sentinel** logo (top) | *nothing — it is a `<div>`, not a link* | ❌ Universal convention is "logo → home". Here it's dead. Cheap, high-value fix. |
| Overview › **Home** | `/` | ✅ |
| Sources › **Connections** | `/connections` | ✅ |
| Sources › **Datasets** | `/datasets` | ✅ |
| Quality › **Checks** | `/checks` | ✅ |
| Quality › **Runs** | `/runs` | ✅ |
| Quality › **Exceptions** | `/exceptions` | ✅ |
| Explore › **Workbench** | `/workbench` | ✅ |
| Explore › **Lineage** | `/lineage` | ✅ |
| Explore › **Assistant** | `/assistant` | ✅ |
| **Settings** (footer) | `/settings` | ✅ Sensibly separated from the task nav. |
| User name / email block | *nothing* | 🔵 Display only. Fine, but it's where users instinctively look for "Profile / change password" — there is no such screen. |
| Role label | *nothing* | 🔵 |
| **Sign out** | `logout()` → hard `window.location.href="/login"` | ✅ Works. (Full reload rather than SPA nav, but that's the correct way to dump auth state.) |

> The nav grouping (Overview / Sources / Quality / Explore) is genuinely good
> information architecture — it mirrors the mental model of the product. ✅

### Topbar

| Element | Click target | Verdict |
|---|---|---|
| **Fleet-health pill** ("Sources healthy" / "N failing" / "No sources") | `navigate("/connections")` | ✅ Polls `/connections/health` every 60s and **shares the `fleet-health` cache key** with ConnectionsPage, so the per-row status badges there are already populated when you arrive. Smart. |
| **Global search** input | debounced `GET /datasets?q=`; results dropdown | ✅ `/` focuses it, `Esc` blurs, `Enter` opens the top hit. |
| Search result row | `navigate("/datasets/:id")` | ✅ |
| **Theme toggle** (moon/sun) | flips `html[data-theme]`, persists to `localStorage` | ✅ |

**Gap:** global search only searches **datasets**. A user hunting for a check, a run, a
connection, or an exception by name has no global entry point. ❌

---

## 2. Login (`/login`)

| Element | Click target | Verdict |
|---|---|---|
| Email / Password fields | — | 🔵 `autoFocus` on email. ✅ |
| **Sign in** | `login()` → on success the app re-renders into `/` | ✅ |
| (Dev hint text) | — | 🔵 Shows seeded creds — fine for dev, **must not ship to prod**. ⚠️ |

No "forgot password", no "request access". Acceptable for an internal tool; note it.

---

## 3. Home — "Data quality overview" (`/`)

The landing pad. Mostly a launchpad of links, which is the right instinct.

| Element | Click target | Verdict |
|---|---|---|
| **Add data** (header button) | `/connections` | ⚠️ Labeled like it opens an "add" form; actually drops you on the Connections **list**. You then hunt for "Add connection". Minor bait-and-switch. |
| Stat card **Datasets monitored** | *nothing* | ❌ Begs to link to `/datasets`. The number is a natural button. |
| Stat card **Active checks** (+ "N proposals awaiting review") | *nothing* | ❌ "Proposals awaiting review" is a call to action with nowhere to click. Should deep-link to proposed checks. |
| Stat card **Failing checks** | *nothing* | ❌ Should link to `/runs?status=fail`. |
| Stat card **Open exceptions** | *nothing* | ❌ Should link to `/exceptions`. |
| "Run results — last 14 days" → **Runs** | `/runs` | ✅ |
| Bars in the run-trend chart | *nothing* (tooltip only) | ⚠️ A day's stacked bar is the obvious place to drill into "that day's failed runs". Non-interactive dataviz. |
| "Datasets needing attention" → **All datasets** | `/datasets` | ✅ |
| A "needs attention" dataset card | `navigate("/datasets/:id/exceptions")` | ✅ Excellent — deep-links straight to the *exceptions* tab, not the generic dataset page. |
| "Recent runs" → **All runs** | `/runs` | ✅ |
| Embedded RunsTable rows | (see §10, RunsTable) | — |

**Read:** the four headline KPIs are the single biggest wasted click surface in the app.
Every other dashboard card is wired; the stat cards are not.

---

## 4. Connections (`/connections`)

| Element | Click target | Verdict |
|---|---|---|
| **Check fleet health** | `health.refetch()` — probes every source, fills Status column | ✅ On-demand by design (probing dozens of DBs is deliberate). |
| **Add connection** (admin) | opens `AddConnectionModal` | ✅ |
| ↳ Engine chip (SQLite, Postgres…) | toggles a DSN template into the field | ✅ Thoughtful — edit a template instead of typing a DSN cold. Dot shows driver-installed. |
| ↳ **Test connection** | `POST /connections/test` | ✅ Validate before commit. |
| ↳ **Save** | `POST /connections` → invalidates list, closes | ✅ |
| Connection **row** (as a whole) | *nothing* | ❌ **Inconsistent.** On `/datasets` the whole row is clickable; here it isn't. Muscle memory fails. |
| **Browse tables** (per row) | `/connections/:id/browse` | ✅ The only way in. |
| **Delete** (admin) | native `confirm()` → `DELETE /connections/:id` | ⚠️ Uses browser `confirm()` everywhere else the app uses its own `Modal`. Jarring, and the warning ("…and its N datasets, checks and history") is buried in a tiny dialog for a highly destructive act. |

**Gaps:**
- ❌ **No connection detail page.** You can't view a connection's datasets, edit its DSN
  (only delete + re-add), rename it, or see health history. A connection is a
  second-class object.
- The DSN cell truncates with no copy / reveal affordance.

---

## 5. Browse tables (`/connections/:id/browse`)

| Element | Click target | Verdict |
|---|---|---|
| **← Connections** | `/connections` | ✅ The app's *only* breadcrumb-style back link. (Tells you how much the rest of the app misses them.) |
| Filter box | local filter | ✅ |
| Table **row** (unregistered) | toggles selection (whole row clickable) | ✅ Checkbox + row both work; row stops propagation correctly. |
| Table row (already registered) | inert; shows… | 🔵 |
| **already registered →** | `/datasets/:id` | ✅ |
| **Register N datasets** (editor) | `POST /datasets/register` → 1 result: `/datasets/:id`; many: `/datasets` | ✅ Smart: single registration jumps straight into the new dataset; bulk drops you on the list. |

**Gap:** ⚠️ After registering, the new dataset is **not auto-profiled** — you land on its
Profile tab's empty state and must click "Profile this dataset". Defensible, but the
hand-off could be smoother (e.g. offer "Register & profile").

---

## 6. Datasets (`/datasets`)

| Element | Click target | Verdict |
|---|---|---|
| **Browse sources** (header) | `/connections` | ✅ |
| Search box | local filter (table / connection / owner) | ✅ |
| Health filter chips (all/fail/warn/pass/unknown) | local filter | ✅ Default sort is by open-exceptions desc — worst first. ✅ |
| Dataset **row** | `navigate("/datasets/:id")` → Profile tab | ✅ Whole-row click. The canonical pattern… that Connections/Checks/Runs don't follow. |
| Empty state **Go to connections** | `/connections` | ✅ |

Clean screen. The inconsistency isn't here — it's that *other* tables don't behave like
this one.

---

## 7. Dataset detail (`/datasets/:id/:tab`) — the workspace

This is where users spend their time: a header + 9 tabs.

### Header

| Element | Click target | Verdict |
|---|---|---|
| Title `schema.table` + health Pill | *nothing* | 🔵 |
| **Workbench** | `/workbench?dataset_id=:id` | ✅ Carries dataset context → Workbench preloads suggestions for it. |
| **Profile now** (editor) | `POST /datasets/:id/profile` | ✅ |
| Tab buttons (Profile … Root cause) | `navigate("/datasets/:id/:tab")` | ✅ Tabs are **URL-routed**, so each is deep-linkable / shareable / bookmarkable. Strong. |

**Gap:** ❌ **No "← Datasets" link and no breadcrumb.** Once you're three tabs deep into a
dataset, the only way back to the list is the sidebar. There's no "Connection › Dataset"
trail, even though both parents exist. This is the app's most pervasive wayfinding hole.

### 7a. Profile tab

| Element | Click target | Verdict |
|---|---|---|
| **Preview rows** / Hide | toggles a 25-row sample (lazy `GET /preview`) | ✅ |
| **Profile this dataset** (empty state, editor) | runs profiling | ✅ |
| AI "Exploration insights" cards | *nothing* | 🔵 Read-only narrative. Fine. |
| Per-column stat cards | *nothing* | ⚠️ A column card is the natural launch point for "add a check on this column" or "chart it". Currently pure display. |

### 7b. Code tab

| Element | Click target | Verdict |
|---|---|---|
| **Copy** | `navigator.clipboard.writeText(ddl)` → "Copied" for 1.6s | ✅ Clean, with graceful failure if clipboard is blocked. |

### 7c. Lineage tab

| Element | Click target | Verdict |
|---|---|---|
| Depth chips **1 / 2 / 3** | re-query at that BFS depth | ✅ Keeps the old graph while loading (no flicker). |
| **Open estate lineage** | `/lineage?connection=:connId` | ✅ |
| Graph **node** (registered) | `navigate("/datasets/:id/lineage")` | ✅ Lands on the neighbor's *lineage* tab — keeps you in lineage context. |
| Graph node (external / unregistered) | inert (cursor unchanged) | ⚠️ Tooltip explains "(not registered as a dataset)", but there's no affordance inviting you to register it. A missed "register this upstream table" hook. |

### 7d. Checks tab

| Element | Click target | Verdict |
|---|---|---|
| **Generate checks (AI/heuristic)** (editor) | `POST /checks/generate`; disabled until profiled (tooltip says why) | ✅ |
| **Explore data first** checkbox | toggles the agentic explorer | ✅ Surfaced inline; clear caption. |
| **New check** (editor) | opens `NewCheckModal` | ✅ The *only* place to hand-author a check (see Checks-page gap). |
| ↳ Create & activate | `POST /checks` | ✅ |
| **Proposed checks** → Activate / Edit / Dismiss | status mutate / edit modal / archive | ✅ Review-and-promote flow is clear and well-labeled. |
| Active checks → Run / Pause / Resume / Edit / Archive | per-row mutate | ✅ |

### 7e. Runs tab → see RunsTable (§10). 7f. Exceptions tab → see ExceptionsTriage (§11).

### 7g. Dashboards tab

| Element | Click target | Verdict |
|---|---|---|
| Focus input + **Generate dashboard** (editor) | `POST /adhoc-dashboards/generate`, then auto-opens it | ✅ |
| Saved dashboard (left list) | `setOpenId` → loads + re-runs panels live | ✅ |
| Panel **Show SQL** (book icon) | toggles the panel's SQL | ✅ Good transparency — every chart shows its query. |
| **Refresh** | re-runs panels | ✅ |
| **Delete** (editor) | `DELETE /adhoc-dashboards/:id` — no confirm | ⚠️ Destructive, no confirmation. |

### 7h. Knowledge tab

| Element | Click target | Verdict |
|---|---|---|
| Form fields (context, issues, importance, owner, SLA, PII, notes) | local form; `fieldset disabled` for viewers | ✅ Disabling the whole fieldset for viewers is the clean way to do read-only. |
| **Save knowledge** (editor) | `PUT /datasets/:id/knowledge` → "Saved." | ✅ The "How knowledge is used" side panel is excellent onboarding. |

### 7i. RCA / Root cause tab

| Element | Click target | Verdict |
|---|---|---|
| Question input + **Investigate** (editor+LLM) | `POST /rca/start` → polls every 4s while running | ✅ |
| Session card | display; auto-refreshes | 🔵 |
| **Investigation transcript** (`<details>`) | expands the agent's SQL trace | ✅ Evidence is inspectable — exactly right for trust. |

> **Cross-tab gap:** the tab strip never reflects state — e.g. Exceptions doesn't show a
> count badge, RCA doesn't show "running", Checks doesn't show "3 proposed". You have to
> open each tab to discover there's anything to do. ⚠️

---

## 8. Checks (`/checks`) — global

| Element | Click target | Verdict |
|---|---|---|
| Search box | local filter (name/column/type/dataset) | ✅ |
| Status chips (all/active/proposed/disabled) | refetch by status | ✅ |
| Check **row** | *nothing* | ❌ No check detail page exists. A check is the core domain object and has **no home** — you can't see its run history, its violation trend, or a permalink to it. |
| Dataset name (per row) | `/datasets/:id/checks` | ✅ The only outbound link. |
| Run / Pause / Edit / Archive (editor) | per-row mutate | ✅ |

**Gaps:**
- ❌ **No "New check" button** on the global Checks page. Creation lives *only* inside a
  dataset's Checks tab. A user who thinks "I'll add a check" and clicks Checks in the nav
  hits a wall.
- ❌ **No per-check drill-in.** Runs and exceptions reference a check by name as plain
  text; there's nowhere for that name to point.

---

## 9. Runs (`/runs`) — global

| Element | Click target | Verdict |
|---|---|---|
| Status chips (all/fail/warn/error/pass) | refetch | ✅ Auto-refreshes every 20s. |
| **Root-cause latest failure** (editor+LLM, only if failures exist) | `POST /rca/start` on `failedRuns[0]` → `/datasets/:id/rca` | ⚠️ Only ever investigates the *single newest* failure. There's no per-row "root-cause this one". |
| RunsTable rows | see §10 | — |

---

## 10. RunsTable (shared: Home, Runs, dataset Runs tab)

| Element | Click target | Verdict |
|---|---|---|
| Run **row** (as a whole) | *nothing* | ❌ No run detail page. |
| Status Pill | *nothing* | 🔵 |
| **Check name** | *nothing* | ❌ Plain text — should link to the check (which itself has no page; see §8). |
| Dataset name | `/datasets/:id` | ✅ |
| **N exceptions** | `/exceptions?run_id=:id` | ✅ Deep-links to exactly this run's violating rows. |
| **investigate →** (fail/warn/error rows) | `/workbench?dataset_id=:id&run_id=:id` | ✅ Carries run context → Workbench auto-suggests investigation SQL. |

**Read:** a run's only exits are "its exceptions" and "investigate in workbench". You
cannot get from a run to the **check** that produced it, nor to a dedicated run view. The
row feels clickable (it's a dense data row) but isn't — only two cells are live. ⚠️

> Note the **two different "investigate" verbs**: RunsTable's "investigate →" opens the
> *Workbench* (you write SQL); RunsPage's "Root-cause latest failure" launches the *RCA
> agent* (it writes SQL). Same word, different surfaces — see §14.

---

## 11. ExceptionsTriage (shared: Exceptions page + dataset Exceptions tab)

| Element | Click target | Verdict |
|---|---|---|
| Status chips (all/open/acknowledged/expected/resolved/muted) | refetch | ✅ |
| Header **dataset `<select>`** (Exceptions page only) | rewrites `?dataset_id` in the URL | ✅ URL-driven filter = shareable. |
| Select-all / per-row checkbox | selection (stops row-click propagation) | ✅ |
| Bulk action bar: Acknowledge / Mark expected / Resolve / Mute / Reopen | `POST /exceptions/triage` | ✅ Bulk triage with an optional note. Each action has a hint tooltip. Strong. |
| Exception **row** | opens `RowDetailModal` | ✅ |
| ↳ Modal **Investigate in workbench →** | `/workbench?dataset_id=:id&exception_id=:id` | ✅ |
| ↳ Modal "run #N", "check_name" | *nothing* | ❌ Both are plain text. Can't click through to the run or check that generated the exception. |

---

## 12. Workbench (`/workbench`)

Accepts `?dataset_id ?run_id ?exception_id ?check_id ?connection_id` and turns them into
context-aware query **suggestions** — the connective tissue that makes "investigate →"
links pay off.

| Element | Click target | Verdict |
|---|---|---|
| Connection `<select>` | switches active source | ✅ Auto-selects from dataset context or first connection. |
| Schema table name (▸/▾) | expand/collapse columns | ✅ |
| **+** icon (per table) | inserts name into SQL | ✅ |
| **book** icon (per table) | opens DDL modal | ✅ |
| Column row | inserts column name into SQL | ✅ |
| SQL textarea | — | ✅ `Ctrl/Cmd+Enter` runs. |
| **Run** (editor) | `POST /query/run` | ✅ |
| limit `<select>` | 50–2000 | ✅ |
| **Chart / Table** toggle | local view switch (only when ≥2 cols + a numeric col) | ✅ |
| Chart type / X / Y selects | reconfigure viz | ✅ |
| Suggestion **Run** (editor) | sets SQL + runs | ✅ |
| Suggestion **Edit** | sets SQL only (no run) | ⚠️ For **viewers**, "Run" is hidden but "Edit" remains — it loads SQL into an editor whose Run button is disabled. A polite dead-end. |

**Gaps:** ⚠️ Insert logic appends raw tokens (`… colname`) after the first
`SELECT * FROM x LIMIT 50`, which can produce invalid SQL on the second click. No query
history / save. No "create a check from this query" — a natural bridge that's missing.

---

## 13. Lineage (`/lineage`) — estate-wide

| Element | Click target | Verdict |
|---|---|---|
| Connection `<select>` | writes `?connection=` (shareable) | ✅ |
| Graph **node** (registered) | `/datasets/:id/lineage` | ✅ |
| Graph node (external) | inert | ⚠️ Same "no register-me hook" as the dataset Lineage tab. |
| "Needs attention" item (registered) | `/datasets/:id` | ⚠️ Goes to the **Profile** tab, while the graph node goes to the **lineage** tab — two destinations for "open this table". Pick one. |
| Edge-table **open dataset** | `/datasets/:id` | ✅ Only shown when the target is registered. |

---

## 14. Assistant (`/assistant`)

| Element | Click target | Verdict |
|---|---|---|
| **New conversation** (editor) | `POST /chat/sessions` | ✅ |
| Session list item | `setSessionId` → loads thread over WebSocket | ✅ |
| Session **×** (delete) | `DELETE /chat/sessions/:id` (stops propagation) | ⚠️ No confirm. |
| Suggestion chips ("What's broken right now?" …) | `startWith()` — creates a session if needed, then sends | ✅ Great cold-start. |
| Composer textarea | `Enter` sends, `Shift+Enter` newline | ✅ |
| **Send / Stop** | send message / `socket.stop()` | ✅ Mid-stream stop is a nice touch. |
| **Reconnect** (on mid-turn disconnect) | `socket.reconnect()` | ✅ Handles the WS failure mode explicitly. |
| Inline chart / SQL `<details>` in replies | expand | ✅ |

The whole page is editor-gated; **viewers** land on a fully-disabled screen with two info
boxes. ⚠️ Honest, but a viewer gets no value at all.

---

## 15. Settings (`/settings`)

| Element | Click target | Verdict |
|---|---|---|
| Health / LLM / "Signed in as" stat cards | *nothing* | 🔵 Display. ✅ |
| **MCP › Add server** (admin) | modal → `POST /mcp-servers` | ✅ |
| MCP **Enable/Disable** | `PATCH` toggle | ✅ |
| MCP **Delete** (admin) | `DELETE` — no confirm | ⚠️ |
| **Invite user** (admin) | modal → `POST /auth/users` | ✅ |
| User **role `<select>`** | `PATCH` **on change, immediately** | ❌ Privilege change with no confirm and no undo. Mis-click promotes someone to admin or demotes them silently. Your own row is correctly disabled. ✅ for the self-guard, ❌ for the no-confirm. |
| **Activate / Deactivate** user | `PATCH` toggle | ⚠️ No confirm; at least it's reversible. |

---

## 16. The intended flow (and whether you can click it end-to-end)

```
Home ──Add data──▶ Connections ──Add connection──▶ (modal: Engine→Test→Save)
   │                    │
   │                    └──Browse tables──▶ Browse ──Register N──▶ Dataset (Profile tab)
   │                                                                   │
   │                                       Profile now ◀───────────────┘
   │                                                                   │
   │                                     Checks tab ──Generate / New──▶ Activate
   │                                                                   │
   │                          (worker runs on schedule) ──▶ Runs ──▶ failures
   │                                                                   │
   │                              N exceptions ◀──────────────────────┤
   │                                   │                               │
   │                              Triage (Ack/Expected/Resolve/Mute)   │
   │                                                                   │
   │                              investigate → Workbench  /  RCA agent (Root cause tab)
   │
   └── Knowledge tab feeds every AI step · Lineage / Assistant are lateral explore tools
```

The **happy path is clickable end-to-end** — that's a real strength. Context flows
correctly through the hard part (run/exception → workbench suggestions). The weaknesses
are all in *return trips* and *lateral jumps*, not the forward funnel.

---

## 17. Gaps, prioritized

### P0 — wayfinding holes that hit everyone, every session
1. **No breadcrumbs / no parent link on the dataset workspace.** Three tabs deep, the
   only way "up" is the sidebar. Add `Connection › schema.table` (both segments
   clickable) to the dataset header. *(One component, used by the most-visited screen.)*
2. **Logo isn't a link.** Make it `→ /`. Trivial, expected everywhere.

### P1 — first-class objects with no home
3. **Checks have no detail page and no global "New check."** The nav item "Checks" can
   only filter/manage, not create or drill in. Add a check detail (its config + run
   history + violation trend) and a top-level "New check".
4. **Runs have no detail page; check name is dead text.** Link run → check, and give a
   run its own view (or at least make the row open the exceptions/transcript).

### P2 — inconsistencies that break learned behavior
5. **Row-click is clickable on Datasets but inert on Connections / Checks / Runs.** Pick
   one rule (recommended: whole-row navigates; keep action buttons stop-propagation).
6. **Two destinations for "open this table"** in Lineage (Profile tab vs Lineage tab) and
   **two meanings of "investigate"** (Workbench vs RCA). Disambiguate the labels.
7. **`confirm()` for connection delete vs the app's own Modal everywhere else.** Move all
   destructive confirms into the styled Modal; add confirms to the ones that lack them
   (dashboard delete, MCP delete, user deactivate, **role change**).

### P3 — dead click surfaces (high-intent, no target)
8. **Home KPI cards** (Datasets / Active checks+proposals / Failing / Open exceptions) →
   wire each to its filtered list. Highest-traffic wasted clicks in the app.
9. **Charts are non-interactive** — Home trend bars and column profile cards should
   drill through (day → runs; column → add check / chart).
10. **Global search covers datasets only.** Extend to checks / runs / connections, or
    label it "Search datasets" so it doesn't feel broken.

### P4 — viewer experience & smaller snags
11. Viewers hit polite dead-ends: Assistant fully disabled; Workbench "Edit" loads SQL
    they can't run. Either hide or explain-with-an-upgrade-path.
12. "Add data" (Home) lands on a list, not an add form — relabel "Manage sources" or
    have it open the Add-connection modal directly.
13. Tab strip shows no state badges (proposed-count, open-exceptions, RCA-running).
14. No auto-profile after registering; offer "Register & profile".
15. Workbench has no query history / "save as check"; no permalink to a result.

---

## 18. What's genuinely good (keep these)

- **URL-routed dataset tabs** and **URL-driven filters** (exceptions, lineage) — every
  meaningful view is deep-linkable and shareable.
- **Context-passing deep links**: run / exception → Workbench preloads tailored
  suggestions; failed run → RCA on the right dataset; "needs attention" → the exceptions
  tab specifically. This is the app's best idea, executed well.
- **Shared `fleet-health` cache** so the topbar pill warms the Connections page.
- **Review-and-promote** check flow; **bulk triage** with notes; **inspectable** RCA
  transcripts and panel SQL — transparency that builds trust.
- **Clean role-gating** via hidden controls / disabled `fieldset`.
- **Sensible empty states** that almost always carry a CTA to the next step.
```
