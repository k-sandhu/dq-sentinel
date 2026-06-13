# DQ Sentinel — Unified Broken-Flows List

Last reviewed: 2026-06-13

This merges **two independent UI reviews** of `frontend/src` into one prioritized list of
*broken flows* — journeys that fail, lose data/context, mislead, dead-end, or block the
user without a way forward. It is deliberately narrower than the per-element glossaries
(see [`UI-GLOSSARY.md`](UI-GLOSSARY.md)): a missing-but-nice feature is not a broken flow;
a journey that silently drops the user's work is.

**How to read the tags**

- **[both]** — flagged independently by *both* reviews → high confidence, fix first.
- **[verified]** — confirmed against the actual code in this pass (real bug, not a guess).
- **[analyst-bar]** — directly conflicts with the enterprise / analyst-first design bar.

**Severity model**

| Tier | Bar |
|---|---|
| **P0** | Silent data/context loss, correctness bug, or wayfinding break that hits every session. |
| **P1** | Dead-end objects, analyst-hostile inputs, or inconsistency that breaks learned behavior. |
| **P2** | High-intent dead surfaces, unexplained blocks, missing "what happened / what next." |
| **P3** | Search, account, layout, and polish gaps. |

---

## P0 — Critical: silent loss & pervasive wayfinding breaks

### BF-1 · You go deep and can't get back / lose where you came from  `[both]`
**Flow that breaks:** enter a dataset from search, lineage, a run, an exception, or the
assistant → land in a 9-tab workspace with **no breadcrumb and no back-link**. The only
way "up" is the global sidebar, which forgets your origin. The logo (a natural "home"
target) is an inert `<div>`, not a link.
**Where:** `DatasetDetailPage.tsx` (header), `WorkbenchPage.tsx`, `RcaTab`, filtered
`ExceptionsPage.tsx`; `Layout.tsx` (logo).
**Fix:** add a compact breadcrumb (`Home / Connection / schema.table / tab`) to dataset
detail, Workbench, RCA, and filtered Exceptions; preserve originating context where
possible; make the logo link to `/`.

### BF-2 · Switching the Exceptions dataset filter silently wipes the run filter  `[both] [verified]`
**Flow that breaks:** open `/exceptions?run_id=42` (e.g. via a run's "N exceptions" link),
then pick a dataset from the dropdown → the handler builds a **fresh** `URLSearchParams`
and sets only `dataset_id`, so `run_id` is dropped. You're now looking at a *different,
broader* set than you think, with no indication the run scope vanished.
**Where:** `ExceptionsPage.tsx` (the `onChange` that calls `setParams(next)`).
**Fix:** mutate the existing params (preserve `run_id`/others) instead of replacing them;
show active filters as removable chips so scope is always visible.

### BF-3 · Unsaved edits vanish with no warning  `[theirs] [verified]`
**Flow that breaks:** fill in the Knowledge tab (business context, known issues, PII,
SLA…) then click another tab → the tab is a router navigation that **unmounts the form**;
all unsaved input is lost silently. Same class of loss on every modal: clicking the
backdrop or **✕** on Add-Connection, New-Check, Edit-Check, and Add-MCP discards typed
input with no "discard changes?" guard.
**Where:** `KnowledgeTab.tsx` (local form state, no navigation guard); `ui.tsx` `Modal`
(backdrop/✕ close); the four form modals.
**Fix:** dirty-state guard — warn on tab-away / modal-close when the form is dirty; or
autosave drafts. The Knowledge tab is the worst because it holds the most work.

### BF-4 · Destructive actions fire on a single mis-click, no confirm, no undo  `[both]`
**Flow that breaks:** one click permanently removes things, often with large blast radius
and zero confirmation:
- Check **Archive** and proposal **Dismiss** — `ChecksTable.tsx`.
- Dashboard **Delete** — `DashboardsTab.tsx`.
- Chat **Delete** (the ✕ on a session) — `AssistantPage.tsx`.
- MCP server **Delete** — `SettingsPage.tsx`.
- User **Deactivate** and **Role change** (mutates *on `<select>` change*, instantly
  promoting/demoting) — `SettingsPage.tsx`.
- Connection **Delete** — has a `window.confirm`, but it cascades datasets + checks +
  history behind a tiny native dialog (`ConnectionsPage.tsx`).
**Fix:** route all destructive actions through the app's own `Modal` confirm (kill the
native `confirm`); add **undo** where cheap; require **typed confirmation** for the
cascading connection delete; make the role `<select>` a deliberate "Save role" action,
not an on-change mutation.

---

## P1 — High: dead-end objects, fragile inputs, inconsistency

### BF-5 · A run isn't a place you can stand on  `[both]`
**Flow that breaks:** you see a failed run but **there is no run detail page/drawer**. The
row is mostly inert — only "N exceptions" and "investigate →" are live; the **check name
is dead text**; **successful runs have no drill-down**; and there's **no per-row "RCA this
run."** Investigation lives only in the transient Workbench, so a failure never becomes a
durable, linkable object.
**Where:** `RunsTable.tsx`, `RunsPage.tsx`, `RunsTab.tsx`.
**Fix:** add `/runs/:id` (or a drawer) with metrics, the violation SQL, its exceptions,
RCA, and the investigate link; make the row open it; add "Start RCA for this run" per row.

### BF-6 · Checks (and connections) are first-class objects with no home  `[mine]`
**Flow that breaks:** a check is referenced everywhere as **plain text** (in runs,
exceptions) but has **no detail page** — no run history, no violation trend, no permalink.
The global `/checks` page can filter and manage but **can't create** (creation is hidden
inside a dataset's Checks tab), so "I'll add a check" from the nav dead-ends. Connections
similarly have no detail view (only browse + delete).
**Where:** `ChecksPage.tsx`, `ChecksTable.tsx`, `RunsTable.tsx`, `ConnectionsPage.tsx`.
**Fix:** add a check detail page and link every check reference to it; add "New check"
(with a dataset picker) to `/checks`; consider a minimal connection detail (its datasets +
health history + rename).

### BF-7 · Check create/edit demands hand-written JSON  `[theirs] [analyst-bar]`
**Flow that breaks:** the New-Check and Edit-Check modals expose **raw JSON params** in a
textarea, only parsed/validated **on submit** — a malformed brace fails the whole save.
For a product whose users are analysts, this is the sharpest mismatch with the
analyst-first bar, and it quietly erodes trust in check setup.
**Where:** `ChecksTab.tsx` (NewCheckModal), `ChecksTable.tsx` (EditCheckModal).
**Fix:** schema-driven fields per check type (the registry already carries param schemas),
inline validation, and a preview; keep raw JSON behind an "advanced" toggle.

### BF-8 · "Open this table" and "investigate" mean different things in different places  `[both]`
**Flow that breaks:** clicking a dataset in **lineage** has three destinations — a graph
node → the *lineage* tab, a "needs attention" item → the *profile* tab, a relationship-row
link → the *profile* tab — so the same intent lands you in different places. Separately,
"**investigate**" means *Workbench* in some spots and the *RCA agent* in others. And
whole-row click navigates on **Datasets** but is inert on **Connections / Checks / Runs**,
breaking the pattern users just learned.
**Where:** `LineageGraph.tsx`, `LineagePage.tsx`, `LineageTab.tsx`; `RunsTable.tsx` vs
`RunsPage.tsx`; `DatasetsPage.tsx` vs `ConnectionsPage.tsx`/`ChecksPage.tsx`.
**Fix:** one default destination per object (recommend: failing/warn nodes → *exceptions*;
others → *profile*), with explicit "Open profile / Open lineage" when both matter;
disambiguate the two "investigate" verbs; make row-click consistent everywhere.

### BF-9 · Building a query by clicking the schema produces invalid SQL  `[theirs] [verified]`
**Flow that breaks:** inserting a table/column from the schema browser appends a **raw,
unquoted identifier** (`… colname`) and the first insert hard-codes `SELECT * FROM <table>
LIMIT 50` **without the schema** — so reserved words, mixed-case, or special-char names
break, and cross-schema tables resolve wrong. Also, **changing the connection leaves stale
SQL/results** on screen, now pointed at the wrong source.
**Where:** `WorkbenchPage.tsx` (`insert` helper; connection `onChange`).
**Fix:** dialect-aware identifier quoting and schema-qualified inserts; clear or flag
results when the connection changes.

---

## P2 — Medium: dead high-intent surfaces, silent blocks, missing feedback

### BF-10 · The most clickable-looking things aren't clickable  `[both]`
- Home **KPI stat cards** ("Open exceptions", "Failing checks", "Active checks / N
  proposals awaiting review", "Datasets") — inert; should deep-link to their filtered
  lists. Highest-traffic wasted clicks in the app.
- Home **trend chart bars** — no day → runs drill-through.
- Profile **column cards** and **PK / temporal badges** — pure display; should offer
  "create unique / freshness check" and "chart this column."
- **Dataset rows** offer no shortcut to their Checks / Runs / Exceptions.
- Plain-text references (`run #N`, dataset name in exceptions) aren't links.
**Where:** `HomePage.tsx`, `ProfileTab.tsx`, `DatasetsPage.tsx`, `ExceptionsTriage.tsx`.

### BF-11 · You're blocked and the UI won't say why or how to recover  `[both]`
**Flow that breaks:** "Generate checks / dashboard" is disabled until a profile exists; the
**Assistant composer** is disabled until a session exists yet its placeholder says "Start a
new conversation…" (implying you can just type); the Runs "Root-cause latest failure"
button **disappears** entirely when the LLM is off or you're a viewer; "Save" on a failed
connection test is enabled but unlabeled. None explain the fix.
**Where:** `ChecksTab.tsx`, `DashboardsTab.tsx`, `AssistantPage.tsx`, `RunsPage.tsx`,
`ConnectionsPage.tsx`.
**Fix:** inline recovery hints next to the blocked control ("Profile first", "Create a
conversation first", "Editor role required", "LLM required"); prefer *disabled-with-reason*
over *hidden*; label the failed-test path "Save anyway."

### BF-12 · After an action, the user doesn't know what happened or where it went  `[both]`
- **Registering multiple datasets** drops you on the full `/datasets` list with no
  "created just now" highlight or summary (`ConnectionBrowsePage.tsx`).
- Newly registered datasets aren't auto-profiled and there's no guided next step.
- Clicking a check's **Run** updates the table but doesn't route you to the resulting run.
- The Dashboards tab doesn't auto-select an existing dashboard → the right pane looks
  empty until you click one (`DashboardsTab.tsx`).
- There's no **first-run onboarding** (Connect → Browse → Register → Profile → Generate)
  even though that's the intended spine.
**Fix:** post-action summaries/highlights, a "Register & profile" option, route-to-result
after Run, auto-open the latest dashboard, and a dismissible first-run checklist on Home.

### BF-13 · Long-running agent work has no controls or status  `[both]`
**Flow that breaks:** an **RCA session has no stop/cancel** (while the Assistant *does* —
an inconsistency); and the dataset **tab strip shows no state** (no "3 proposed", no "12
open exceptions", no "RCA running"), so you must open each tab to discover there's work.
**Where:** `RcaTab.tsx`, `DatasetDetailPage.tsx`.
**Fix:** add stop to RCA; add count/status badges to the tabs.

---

## P3 — Lower: search, account, layout, polish

### BF-14 · Retrieval is thin  `[both]`
Global search covers **datasets only** (not checks, runs, owners, exceptions, commands)
and has **no arrow-key selection**; there are **no saved views** and few filters (checks:
no severity/origin/failing/schedule; runs: no date-range or `running` filter; exceptions:
no saved views). `Layout.tsx`, `ChecksPage.tsx`, `RunsPage.tsx`, `ExceptionsPage.tsx`.

### BF-15 · Account & admin flows are incomplete  `[theirs]`
No forgot/reset-password; **Invite user sets a raw password** with no invite/reset email;
no show-password on login; dev credentials are shown on the login screen (must not ship);
no audit/history for user, MCP, or knowledge changes; MCP has **no "test connection."**
`LoginPage.tsx`, `SettingsPage.tsx`.

### BF-16 · Validation & layout polish  `[mixed]`
Knowledge **PII columns are free text**, not validated against the dataset's real columns
(a redaction-correctness risk). Browse-tables filter ignores schema/kind and lacks
"select all visible." Long DDL has no wrap/search. The Dashboards two-column layout needs a
mobile pass. Connection health shows blank on a cold Connections load until the fleet pill's
first poll (perceived inconsistency vs the auto-polling pill).

---

## Fix-first shortlist (maximum relief per unit work)

1. **Breadcrumbs + clickable logo** (BF-1) — one shared component, every deep screen.
2. **Stop wiping filters / unsaved work** (BF-2, BF-3) — preserve `URLSearchParams`; add a
   dirty-state guard. Pure correctness, low risk.
3. **Confirm/undo on all destructive actions** (BF-4) — and make role-change deliberate.
4. **Run detail drawer** (BF-5) — turns failures into durable, linkable objects.
5. **Schema-driven check forms** (BF-7) — the biggest analyst-first win.
6. **Make Home KPIs + dataset rows actionable** (BF-10) — cheap, high-traffic payoff.
7. **Standardize click destinations** (BF-8) — consistency users feel immediately.

---

## What's already good (don't regress it)

Both reviews agree the **product spine is right** — `Connect → Browse → Register → Profile
→ Generate checks → Runs → Exceptions → RCA/Workbench` — and these patterns are genuinely
strong: clear sidebar IA; URL-routed tabs and URL-driven filters (deep-linkable);
context-passing deep links (run/exception → Workbench suggestions; failure → RCA on the
right dataset); a real triage model (acknowledge / expected / resolved / muted); SQL
transparency everywhere (DDL, panel SQL, RCA transcripts); sensible role gating; and
mostly-handled LLM-disabled states. The gaps above are **usability glue** — context,
consistency, confirmation, and task-oriented next steps — not spine.
