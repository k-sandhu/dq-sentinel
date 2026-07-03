# DQ Sentinel — Unified Broken-Flows List

Last reviewed: 2026-06-19 (prior review: 2026-06-13)

This merges **two independent UI reviews** of `frontend/src` into one prioritized list of
*broken flows* — journeys that fail, lose data/context, mislead, dead-end, or block the
user without a way forward. It is deliberately narrower than the per-element glossaries
(see [`UI-GLOSSARY.md`](UI-GLOSSARY.md)): a missing-but-nice feature is not a broken flow;
a journey that silently drops the user's work is.

> **What changed since 2026-06-13.** The entire P0 fix-first shortlist (BF-1–BF-4) and much
> of P1/P2 have shipped — breadcrumbs + a clickable logo, filter/param preservation, a
> dirty-state guard, a shared typed-confirm dialog, dedicated **run / check / connection
> detail pages**, schema-driven check forms, dialect-aware Workbench SQL, clickable Home
> KPIs, and a rebuilt Exceptions workspace with saved views + keyboard triage. The
> [✅ Resolved](#-resolved-since-the-last-review) section records those; the rest of the
> document is the **narrowed list of what is still broken or thin**.

**How to read the tags**

- **[both]** — flagged independently by *both* reviews → high confidence.
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

## ✅ Resolved since the last review

Confirmed shipped against the current `frontend/src` (and backend where noted). Kept here
so the BF-N identifiers stay traceable.

- **BF-1 · Wayfinding / no way back** — **fixed.** Dataset detail now renders a
  `Breadcrumbs` row (`Datasets › schema.table`, `DatasetDetailPage.tsx`); the sidebar logo
  is a `<Link to="/">` (`Layout.tsx`). *(Title + health pill themselves are still inert —
  minor.)*
- **BF-2 · Exceptions filter wipes the run filter** — **fixed.** The dataset `<select>` now
  clones the existing params (`patchParams` → `new URLSearchParams(params)`), so `run_id`
  survives and renders as a removable chip (`ExceptionsPage.tsx`).
- **BF-3 · Unsaved Knowledge edits vanish** — **fixed** for the worst case: the Knowledge
  tab raises a `dirty` flag and the shell intercepts tab-away with a confirm, plus a
  `beforeunload` guard (`KnowledgeTab.tsx`, `DatasetDetailPage.tsx`). *(A dedicated
  "discard changes?" guard on backdrop/✕ close of the form modals was not separately
  re-verified this pass.)*
- **BF-4 · Destructive actions, no confirm** — **fixed.** A shared promise-based
  `useConfirm()` dialog (`confirm.tsx`) now backs check Archive / proposal Dismiss,
  dashboard Delete, chat Delete, MCP Delete, and user Deactivate; the **role `<select>`**
  confirms before mutating; **connection Delete** requires typing the connection name
  (`typeToConfirm`) and spells out the cascade. *(A handful of lower-stakes prompts still use
  the native `window.confirm` — SLA delete, saved-query delete, the Workbench history-clear /
  query-replace guards, and the unsaved-changes discards on dashboards and the Knowledge tab.)*
- **BF-5 · A run isn't a place you can stand on** — **fixed.** `/runs/:id`
  (`RunDetailPage.tsx`) shows metrics, the violation query (when persisted), exceptions, and
  a per-run **"Start RCA"**; `RunsTable` rows open it (success rows included).
- **BF-6 · Checks/connections with no home** — **mostly fixed.** `/checks/:id`
  (`CheckDetailPage.tsx`, with a violation-trend chart + run history) and `/connections/:id`
  (`ConnectionDetailPage.tsx`, with its datasets + health timeline) now exist. **Still open:**
  no global **"New check"** button, and connection detail can't rename / edit the DSN — see
  **BF-6′** under *P1 — Still open* below.
- **BF-7 · Check forms demand hand-written JSON** — **fixed.** `CheckParamsForm` renders
  schema-driven, per-type fields with inline validation and a live "Effective params"
  preview; raw JSON is behind an **Advanced** toggle.
- **BF-8 · "Open this table" means different things** — **mostly fixed.** Lineage
  destinations are centralized in `lib/lineageNav.ts` (fail/warn → exceptions, else profile;
  external → none); whole-row click is now consistent across Datasets / Connections / Checks
  / Runs.
- **BF-9 · Clicking the schema produces invalid SQL** — **fixed.** Inserts route through
  `lib/sqlIdent.ts` (dialect-aware quoting + schema-qualified refs); changing the connection
  clears stale SQL/results.
- **BF-10 · Most clickable-looking things aren't clickable** — **partially fixed.** Home was
  rebuilt as a scorecard: KPI tiles and trend bars now deep-link to filtered lists. **Still
  open:** profile column cards and dataset rows (see P2 below).
- **BF-14 · Retrieval is thin** — **partially fixed.** Global search now spans
  datasets / checks / connections / saved queries with arrow-key selection and ⌘K; the
  Exceptions workspace gained **saved views**, a rich filter bar, and keyboard triage.

---

## P1 — Still open: dead-end create, fragile inputs, misleading affordances

### BF-6′ · Checks still have no global *create*, and can't be reached from runs/exceptions  `[verified]`
**Flow that breaks:** a user who thinks "I'll add a check" and clicks **Checks** in the nav
finds search + status chips but **no "New check" button** — creation still lives only inside
a dataset's Checks tab (`ChecksPage.tsx` vs `dataset/ChecksTab.tsx`). And although
`/checks/:id` now exists, nothing links *to* it from the places a check is named: a
`RunsTable` row's check name links to the **run**, and the exception detail panel's check
name links to the dataset's **Checks tab**, not to `/checks/:id`.
**Fix:** add "New check" (with a dataset picker) to `/checks`; point check-name references in
`RunsTable` and `DetailPanel` at `/checks/:id`.

### BF-7′ · Knowledge PII columns are unvalidated free text  `[verified] [analyst-bar]`
**Flow that breaks:** the Knowledge tab's **PII columns** field is a comma-separated text
input, never validated against the dataset's real columns (`KnowledgeTab.tsx`). A typo means
a column the user *thinks* is redacted isn't — a redaction-correctness risk, since these
names drive prompt redaction.
**Fix:** validate / autocomplete PII columns against the profiled column list; warn on names
that don't match.

### BF-13′ · A running RCA can't be stopped  `[verified]`
**Flow that breaks:** an **RCA session has no stop/cancel** — it just polls every 4s until
done (`RcaTab.tsx`), even though the Assistant *can* stop mid-turn (an inconsistency).
**Fix:** add a stop/cancel control to a running RCA session.

> **Resolved 2026-06-19.** The RCA tab's tip used to claim you could "launch an RCA from any
> failed run on the Runs tab," but the Runs-table row link opens the **Workbench**. The tip
> (and the empty-state hint) now point to a run's **detail page** — open a failed run and
> choose **Start RCA**, which is where per-run RCA actually lives (`RcaTab.tsx`,
> `RunDetailPage.tsx`).

---

## P2 — Still open: dead high-intent surfaces, missing "what next"

### BF-10′ · High-intent surfaces that still aren't actionable  `[both]`
- Profile **column cards** and **PK / temporal badges** — pure display; should offer
  "create unique / freshness / drift check" and "chart this column" (`ProfileTab.tsx`).
- **Dataset rows** still navigate only to the overview — no shortcut to a dataset's Checks /
  Runs / Exceptions, and the **connection cell isn't a link** (`DatasetsPage.tsx`).

### BF-12′ · After an action, the hand-off is still rough  `[both]`
- **Registering datasets** does not auto-profile and offers no "Register & profile" — you
  land on an empty Profile tab and must click "Profile this dataset" (`ConnectionBrowsePage.tsx`).
- The **Dashboards tab** still doesn't auto-select an existing dashboard → the right pane is
  empty until you click one (`DashboardsTab.tsx`).

### BF-13″ · The dataset tab strip still carries no per-tab state  `[verified]`
**Flow that breaks:** the tab buttons are label-only — no "3 proposed", no "12 open", no "RCA
running" (`DatasetDetailPage.tsx`). Active-check and open-exception counts now appear in the
header **sub-line**, but you still must open each tab to discover work inside it.
**Fix:** add count/status badges to the tabs (the header already computes some of the counts).

### BF-8′ · Lineage external nodes explain themselves but still dead-end  `[verified]`
**Flow that breaks:** clicking an external / unregistered node now shows
*"register it as a dataset to open it"* — better than the old silent inert node — but there
is still **no one-click "register this table"** action from the graph (`LineageGraph.tsx`).
**Fix:** add a "Register as dataset" button to the external-node detail panel.

### BF-5′ · RCA on the Runs *page* still only targets the newest failure  `[verified]`
**Flow that breaks:** RunsPage's "Root-cause latest failure" still acts on `failedRuns[0]`
only, and is hidden entirely for viewers / when the LLM is off (`RunsPage.tsx`). The per-run
"Start RCA" on `/runs/:id` mitigates this, but the page-level shortcut is unchanged.
**Fix:** offer per-row RCA from the runs list, or a *disabled-with-reason* button when blocked.

---

## P3 — Still open: account, layout, polish

### BF-15′ · Account flows are incomplete  `[verified]`
No forgot/reset-password; no show-password on login; **dev credentials are printed on the
login screen** (`admin@example.com / admin123` — must not ship to prod). **Invite user sets a
raw password** with no invite/reset email; **MCP "Add server" has no "test connection."**
`LoginPage.tsx`, `SettingsPage.tsx`.

### BF-16′ · Input & layout polish  `[mixed]`
Browse-tables filter ignores **schema** and lacks **"select all visible."** Long DDL on the
Code tab has **no wrap/search**, and the DDL card has no "Open in Workbench" (a *Pinned
queries* card does offer per-query "Open in workbench →"). The Dashboards two-column layout
still needs a mobile pass.

---

## Fix-first shortlist (maximum relief per unit work)

1. **Global "New check" + link check references to `/checks/:id`** (BF-6′) — closes the last
   first-class-object gap now that the detail page exists.
2. **Validate Knowledge PII columns** (BF-7′) — a correctness/redaction risk, cheap to fix.
3. **Stop/cancel RCA** (BF-13′) — a running session still can't be cancelled.
4. **Tab-strip state badges** (BF-13″) — counts are already computed in the header.
5. **Make profile column cards + dataset rows actionable** (BF-10′) — high-traffic payoff.
6. **Pull dev credentials off the login screen** (BF-15′) — must not ship.

---

## What's already good (don't regress it)

Both reviews agree the **product spine is right** — `Connect → Browse → Register → Profile
→ Generate checks → Runs → Exceptions → RCA/Workbench` — and the patterns that were strong a
review ago are now reinforced: breadcrumbs + a clickable logo; URL-routed tabs and
URL-driven filters (deep-linkable); context-passing deep links (run/exception → Workbench
suggestions; failure → RCA on the right dataset); dedicated run/check/connection **detail
pages**; a real triage model with **saved views and keyboard triage**; schema-driven check
forms; SQL transparency everywhere (DDL, panel SQL, RCA transcripts); a shared **confirm**
dialog on the high-stakes destructive actions; sensible role gating; and mostly-handled LLM-disabled
states. The gaps above are now mostly **last-mile glue** — create-from-nav, actionable
display surfaces, tab badges, and account polish — not spine.
