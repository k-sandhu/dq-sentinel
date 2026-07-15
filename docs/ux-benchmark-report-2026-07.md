# DQ Sentinel — UX Benchmark Report (July 2026)

> Results of the pre-registered plan in
> [`ux-benchmark-plan-2026-07.md`](ux-benchmark-plan-2026-07.md). Executed 2026-07-15
> against `main@cda2e16` (freshly rebuilt Docker images; UI `:3002`, API `:18002`,
> seeded demo DB upgraded via Alembic 0009→0011), in real Chrome at 1440×900, light and
> dark themes, admin role, **LLM deliberately disabled**.
>
> ⚠️ Mid-run correction: the standing demo containers were running a **pre-#205/#218
> build**. All findings were re-verified after rebuilding at HEAD; anything fixed by
> #218 was discarded. Two early findings (deep-link loss, "New today" badge) were
> re-confirmed on HEAD, the badge also in source.

## Verdict

**Overall: 4.2 / 5 — genuinely strong enterprise-analyst UX with a handful of
trust-denting defects.** The core triage loop (exceptions workspace), the catalog
one-click onboarding journey, and the workbench are competitive with best-in-class ops
tools. What keeps it from 4.5+: two P1s that show a *wrong number* / *lose the user's
destination*, and a small cluster of dead-end states (infinite spinner on bad ids, blank
lineage pane) that violate the app's otherwise excellent "every state is designed" bar.

### What is genuinely excellent (keep and defend)

- **Exceptions workspace** — saved views, live facet counts, URL-encoded filters *and
  selection* (`?sel=`), one-click lifecycle + note with explicit semantics, bulk bar with
  assignment, full single-key triage (`o/Enter`, `a/e/r/m/u`, `Shift+A`, `?` help
  overlay), append-only Activity trail. This is the flagship, and it feels like it.
- **Honest degradation** — with no LLM configured: header chip ("LLM disabled —
  heuristic mode"), disabled Start-RCA *with tooltip*, RCA/Assistant banners with exact
  env-vars, instant clear failure if you send anyway, heuristic check-gen clearly labeled
  ("via heuristics, 4 duplicates skipped"). #266's silent-off is fixed; this is now a
  model implementation.
- **Catalog → monitored estate journey (J2)** — connect → browse tables ("already
  registered" marked) → register ("Register 1 dataset" count-aware button) → lands on the
  new dataset → profile in ~1s with **honest sampling label** → auto monitor-pack
  ("System"-attributed) → evidence-based proposals with per-proposal rationale →
  activate → run → "pass · just now", all in under a minute with feedback at every hop.
- **Empty states** — nearly every tab/pane explains *why* it's empty and what to do next
  (Profile, Contract, Monitors-freshness, Code/pinned queries, Biggest movers, run
  exceptions…). The two exceptions to this rule are P2 findings below.
- **Workbench** — read-only + single-statement + timeout guardrails stated inline,
  Ctrl+Enter hint, 13 ms query with row count, Filter/Copy/CSV/JSON/Chart on results,
  per-connection saved queries, multi-tab, schema tree.
- **Enterprise spine** — append-only audit log ("Secrets, DSNs, and source rows are never
  recorded") with entity/action/since filters; roles on users; connection forms that
  state read-only session enforcement; skip-to-content link; visible focus rings.

## Scorecard

| Area | Score | One-line justification |
|---|---|---|
| A1 Login & first contact | 3.5 | Clean, honest errors, keyboard-friendly; **loses deep link** (P1-2) |
| A2 Home | 4.5 | Honest chips/windows, drill-down links; donut inert, no tz markers |
| A3 My Work | 4.5 | Honest zero-state day ("0 new · 0 resolved"), direct triage links |
| A4 Dashboards | 4 | Solid builder (cap disclosed, Esc, arrows); cards aren't links; naming drift |
| A5 Status | 4.5 | 10-second legible, counts reconcile; missing "8 of 13" footnote |
| A6 Data catalog | 4.5 | Outstanding cards + one-click flow; morphing check-count confuses |
| A7 Connections | 4 | Test/browse/register excellent; stale fleet-health display (P2-7) |
| A8 Datasets list | 4 | Honest counts, instant search, live favorites; no column sorting |
| A9 Dataset detail (12 tabs) | 5 | Every tab designed; URL = tab; Knowledge tab explains its own purpose |
| A10 Checks | 3.5 | Rich rows/provenance; proposal wall + past NEXT RUN + detail lacks edit |
| A11 Runs | 3.5 | Honest history; raw driver error without remediation (P2-6); no duration |
| A12 Exceptions triage | 4 | Best-in-class workspace; marred by wrong "New today" badge (P1-1) |
| A13 Incidents | 4 | Live rollup, context links, instant ack; "Close" verb ambiguity |
| A14 Reliability | 4 | Honest windows/evaluation age; "budget 100%" undefined |
| A15 Workbench | 5 | See above — nothing material to fix |
| A16 Lineage (global) | 3 | Good graph + URL scoping; **blank pane** when no lineage (P2-4) |
| A17 Assistant | 4.5 | Model LLM-off handling; failed sends still pollute history |
| A18 Settings | 4.5 | Audit log is exemplary; no Appearance section (toggle is top-bar only) |
| A19 Global chrome | 4 | Palette fast/grouped/Enter-navigates; **infinite spinner on bad id** (P2-3) |
| A20 Docs & Features | 4 | Omnipresent floating launcher; content honesty not audited |

| Pillar | Score |
|---|---|
| U1 Orientation & IA | 4.5 |
| U2 Workflow efficiency | 4 |
| U3 Findability & information scent | 4 |
| U4 System status & feedback | 4 |
| U5 Error prevention & recovery | 4 |
| U6 Consistency & standards | 4 |
| U7 Accessibility & keyboard | 4 |
| U8 Trust, honesty & degradation | 4 |

## Journey results

| Journey | Result |
|---|---|
| J1 Morning triage | **Pass.** Login → Home → dataset exceptions → resolve w/ note + bulk-ack ≈ 9 interactions; no state loss; facets update live |
| J2 Onboard a source | **Pass, exemplary.** Catalog → running checks < 60 s, feedback at every stage |
| J3 Incident to cause | **Pass w/ note.** Incident modal → exceptions/dataset links work; RCA honestly gated by LLM-off |
| J4 Cold deep link | **Fail.** Post-login redirect discards destination (finding P1-2) |
| J5 Find one thing fast | **Pass.** Palette: type partial name → grouped results → Enter → detail, ~3 s |

## Findings

### P1 — trust/core-flow defects

1. **"New today" saved-view badge shows the all-open count, not the view's count.**
   Exceptions page chip advertised **296**; applying it returns **0 rows** ("No
   exceptions match these filters"). Root cause in
   [`SavedViews.tsx:20-22`](../frontend/src/components/exceptions/SavedViews.tsx): the
   chip's params are `recurrence=new&status=open` but its `count` callback returns
   `f.status.open`. My Work correctly shows "0 new · last 24h" on the same data — the
   badge is the only lying surface, and it lies on the analyst's primary screen.
   *Fix:* count from the same facet the filter targets, or drop the badge (the "My open"
   chip already omits its count for exactly this reason — see its code comment).

2. **Deep links are lost through login.** Visiting `/checks` (or any URL) while logged
   out redirects to `/login`, and successful auth always lands on `/` (or the configured
   landing page). Re-verified on HEAD. This breaks the data-engineer persona's main entry
   path (assignment links pasted in Slack) and J4. *Fix:* preserve the intended location
   (e.g. `state.from` on the redirect) and navigate there after auth.

### P2 — major friction / dishonest states

3. **Nonexistent dataset id → infinite spinner.** `/datasets/99999` shows
   "Loading dataset…" forever (>10 s observed, no error state, no way back). Stale links
   to deleted datasets will strand users. Give a designed not-found with links back.
4. **Global Lineage renders a completely blank pane** when the selected connection has
   no lineage (e.g. Meridian default): no canvas, no message, no CTA — the only
   blank-pane violation in the app. (Dataset-scoped lineage works.)
5. **"NEXT RUN Jul 5, 08:45 PM" displayed 10 days in the past** on check detail with no
   overdue/scheduler-idle cue. An analyst can't tell whether scheduling is broken or the
   label is. Show "overdue since …" / "scheduler idle" when `next_run < now`.
6. **Errored runs surface raw driver errors with no remediation path.** Run detail shows
   `OperationalError: (sqlite3.OperationalError) unable to open database file…` — no
   "connection appears down — test it" hint, no link to the connection. (UX-adjacent to
   #262 but distinct: this is about the error *presentation*.)
7. **Fleet health can contradict fresh evidence.** Connections header said
   "fleet: **0/4 reachable**" minutes after Retail's health check passed on its detail
   page (row showed "—"); an explicit "Check fleet health" reconciled to 1/5. The
   cached fleet figure carries no "as of …" label.
8. **Global Checks page buries the product under 133 pending proposals** with no bulk
   activate/dismiss and no per-dataset grouping; the active-rules table starts below
   ~15 screens of proposals. (The "proposed" filter chip exists but the *default* view
   leads with the wall.)
9. ~~Default theme is inconsistent.~~ **Withdrawn after investigation:** the
   light-themed tab was running a stale browser-cached bundle from before the mid-run
   image rebuild (its DOM contained a "Toggle color theme" control that no longer
   exists in HEAD). HEAD's appearance system (pre-paint bootstrap, `system` default,
   OS-change listener, cross-tab `storage` sync) is correct; no fix needed.

### P3 — polish with real user impact (selected)

10. Absolute timestamps ("Jul 5, 03:23 PM") carry no timezone marker or tooltip anywhere
    (values are correct local time via `fmtDateTime`); screenshots shared across
    timezones will mislead.
11. Datasets/checks/runs tables have no column sorting (no affordance, no `aria-sort`);
    default risk-first order mitigates.
12. Catalog card inventory silently morphs after connect ("8 checks" → "24 checks")
    — curated vs live counts unexplained; also "Open source" reads as "open-source".
13. Dashboard cards are `<button>`s, not links — no middle-click/copy-link; Home tiles
    are real `<a>`s (inconsistent).
14. "View in **workspace** →" (dashboard widget) vs "Investigate in **workbench** →"
    everywhere else.
15. Incident modal's "Close" button (dismiss) sits beside "Acknowledge/Resolve"
    (lifecycle verbs) — in incident vocabulary "close" *is* a lifecycle action.
16. Incidents filters need an explicit "Filter" button; Exceptions filters apply live —
    two paradigms for the same problem.
17. Reliability "budget 100%" chip is undefined (consumed vs remaining?); MTTD/MTTR and
    SLA cards otherwise label windows well.
18. Detail-panel Activity timeline doesn't refresh after your own triage action (appears
    on reopen; chip + row do update instantly).
19. Modal close returns focus to `<body>`, not the trigger (Add-widget modal); Esc works.
20. Exceptions empty state lacks an inline "clear filters" CTA (Clear all is elsewhere);
    filter panel reflows when facet counts vanish under an active view.
21. Registering a table auto-attaches ~6 "System" monitors with no toast/summary; you
    discover them by noticing the header count change (provenance chips do explain
    after the fact).
22. Check detail page lacks the Edit/Pause/Archive actions its own list rows have.
23. Runs (list + detail) never show duration.
24. Workbench schema panel says "No tables on this connection" for an *unreachable*
    connection — can't-know stated as known-empty.
25. Assistant with LLM off still creates a saved conversation per failed send; history
    fills with "2 messages · just now" junk.
26. RCA/Assistant LLM-off banners speak env-var jargon to all roles; viewers/analysts
    can't act on `DQ_LLM_API_KEY` — vary copy by role or point at an admin.
27. Status page shows only scored datasets (8) with no "8 of 13 monitored" note
    (Home's Coverage tile does disclose 8/13).
28. Home "Checks passing" donut is the one KPI tile that isn't a link.
29. Sidebar health pill ("4 failing") counts failing *connections* but reads as a
    generic failure count next to "FAILING CHECKS 10" surfaces; it needs a label on
    hover at minimum. (Its number does reconcile with fleet health.)
30. Unvirtualized big lists (133 proposals, 296-row tables paginate at 50 but the
    checks proposal wall doesn't) make some pages heavy; screenshot/automation tooling
    visibly struggled on exactly those pages.

### Known bugs re-observed (deduped, not re-filed)

| Known issue | Status at HEAD |
|---|---|
| #262 errored checks → breached with 0 exceptions | UX consequence still visible: `custodian_raw_position` "breached · score 0 · **0 exceptions**" on Home/risk lists with no cue that the cause is *errored* checks |
| #273 search `q` doesn't escape LIKE wildcards | **Verified still open:** searching `%` matches all 300 exceptions |
| #256 proposals normalize observed dirt | Padded-bounds proposals offering `min=-8089.5` for an id column, rationale transparently shows it |
| #261/#257 | Not exercisable (worker off in this environment) |

## Method notes & deviations from plan

- Deliberately not executed: destructive deletes (A4.4, A7.5, A10.5 blast-radius
  confirms), CSV download click (A12.8), viewer-role pass, A15.3/A15.4, A20.2 content
  audit, full C1 keyboard-only journey (CDP key events were unreliable in this
  environment; keyboard support was verified by code inspection + spot checks:
  skip-link, focus rings, palette Enter, login tab order, Esc handling, triage
  shortcut map + `?` help overlay).
- C7 console hygiene: no errors captured during the monitored stretch (partial coverage).
- The old wedged-tab episodes early in the run were traced to the automation tooling +
  heavy pages, not an app deadlock (a queued click executed once the tab recovered).

## Top recommendations, ranked by analyst-time saved

1. Fix the "New today" badge (P1-1) — one-line change; it currently teaches analysts to
   distrust every count in the app.
2. Preserve deep links through login (P1-2) — unblocks the assignment-link workflow.
3. Designed not-found for bad ids + lineage empty state (P2-3, P2-4) — kills the two
   dead-ends.
4. Overdue cue for past next-run + remediation hint on errored runs (P2-5, P2-6) —
   these two together answer "is monitoring actually running?", the #1 operator doubt.
5. Bulk actions + default collapse for check proposals (P2-8).
6. A `· UTC`/local marker or tooltip on absolute timestamps (P3-10) and the handful of
   naming unifications (workbench/workspace, incident "Close") — cheap consistency wins.
