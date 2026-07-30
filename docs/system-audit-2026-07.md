# DQ Sentinel — End-to-End System Audit (2026-07-16)

> **What this is.** A full-system audit: what DQ Sentinel is *supposed* to be, what it
> *actually* is, how it measures against what the market's real users need in 2026, and two
> sets of ten recommendations — one **strategic** (how the product should evolve) and one
> **concrete** (specific updates the audit surfaced). Grounded in a full read of the codebase,
> a live click-through of the running demo, four parallel deep-dive audits (backend, frontend,
> tests/CI/infra, contract-drift), and current market research.
>
> **Method.** Findings carry `file:line` evidence verified against current code. Everything is
> **de-duplicated against the existing tracker** (issues #24–#275, `docs/BROKEN-FLOWS.md`,
> `docs/codebase-deep-dive-action-plan.md`, the 2026-07 UX benchmark). New/verified items are
> tagged; already-tracked items are folded into recommendations as "confirmed still-live," not
> re-filed. Tag legend: **[NEW]** not in the tracker · **[LIVE]** verified on the running demo ·
> **[tracked #N]** existing issue.

---

## 1. What the system is supposed to be

**One sentence (the product's own north star):**

> *Open, self-hostable data quality with agents that **investigate** your data — not just watch
> it. Read-only by design, your LLM or ours, across 9 engines.*

**Who it is for** (from the standing design bar): the **primary user is a DQ analyst who lives in
the tool all day**; secondary users are **data engineers** receiving assignments and
**leads/stakeholders** reading status. It is explicitly meant to be an **enterprise** system —
WCAG AA, keyboard-operable, never silently destroys analyst state, honest UTC time labeling.

**What it is supposed to do** (the intended spine):

1. **Connect** read-only to a SQL source (9 engines via SQLAlchemy DSNs).
2. **Register** tables/views as datasets and **profile** them.
3. **Generate checks** from the profile — heuristics always, LLM-proposed when a key is set,
   with a pre-proposal **exploration agent** that writes read-only SQL to learn the data first.
4. **Run checks on a schedule** (worker) and capture **exceptions** (violating rows).
5. **Triage** exceptions through a lifecycle (open → acknowledged / expected / resolved / muted),
   with "expected" feeding back into a knowledge base that then feeds the AI.
6. **Investigate** failures with an agentic **RCA** (read-only SQL tool-loop, evidence-backed
   report) and a conversational **assistant**.
7. Roll failures up into **incidents**, track **SLAs/error budgets**, enforce **data contracts**,
   and route **alerts** to Slack/Teams/PagerDuty/Jira/ServiceNow.

**How it is supposed to feel** (design philosophy): answer "are we OK?" in five seconds
(status → trend → worklist); one object, all its facets; progressive disclosure via drawers;
**show the work** (every AI/ML output ships its evidence); calm by default, dense on demand;
**restraint is a feature** — a *quality* tool, not a catalog/FinOps/MDM suite; **≤3 nav groups**
so the sidebar never reads as enterprise sprawl.

**Where it means to sit in the market:** *a self-hostable, AI-agent-forward blend of Soda Cloud +
a lightweight Monte Carlo, wearing a Metabase-style triage UI* — best fit for mid-scale data teams
(~10–200 datasets) who want open-source control, data residency, and LLM-assisted diagnostics
without Monte-Carlo/Anomalo pricing.

---

## 2. What the system actually is

**Verdict: the intended spine is fully built and, in several places, better than the spec.** This
is a genuinely capable platform, not a prototype. The live demo exposes **25 routes + a 12-tab
dataset detail page**, **14 first-class check types**, **9 SQL engines**, a managed monitor pack,
ODCS data contracts with enforcement, SLA/error-budget tracking, an incident lifecycle, a
provider-agnostic LLM layer (Anthropic *or* any OpenAI-compatible endpoint incl. **local models**),
and a self-observability stack (Prometheus/Grafana/Loki). Test scaffolding is serious: **46 backend
test files (~13k lines)** with *exemplary* authz negative-path coverage.

### 2.1 Surface maturity (from the live walkthrough + code)

| Surface | Grade | Note |
|---|---|---|
| Exceptions triage workspace | **A** | Best-in-class: server paging, live facets, saved views w/ absolute counts, keyboard triage, bulk ops, CSV, concurrency toasts. The product's crown jewel. |
| Data catalog onboarding | **A** | 7 curated governed domains, one-click connect → running checks in <60s. |
| My work / Status / Runs detail / Schema / Monitors / Lineage tab / Assistant (degradation) | **A/A-** | Solid, honest states, graceful LLM-off messaging with exact env vars. |
| Home / Datasets / Incidents / Reliability / Workbench / Settings | **A-/B+** | Strong but each has a rough edge (see §5). |
| Global **Checks** page | **B+** | Proposal-wall collapse + version history + rollback, but **still no "New check" button** [tracked BF-6′, LIVE]. |
| **Contract** tab | **B/C** | Powerful (ODCS editor, YAML, versions, enforcement) but the hand-edit column editor **loses focus every keystroke** [NEW F-1] and conformance can hard-500 [LIVE]. |
| Login | **B** | Dev credentials still printed on screen [tracked BF-15′, LIVE]. |

### 2.2 Where the actuals diverge from the intent

- **The IA outgrew its own restraint principle.** The design philosophy mandates **≤3 nav groups /
  ≤12 items**; the shipped app has **4 labeled groups (Overview / Sources / Quality / Explore) +
  Settings + dynamic Favorites/Recents ≈ 19 destinations** [LIVE]. Still coherent, but drifting
  toward the "enterprise sprawl" the philosophy explicitly warns against.
- **"Show the work" is undercut in two flagship AI surfaces.** The **structured RCA report** UI
  (hypotheses / evidence / confidence pills) is **dead code** — the backend never produces the
  `report_json` it consumes [NEW/verified, ties to tracked #60]. Analysts only ever see the
  markdown fallback, and the agent's own `confidence`/`suggested_fixes` are dropped by the API
  schema.
- **"Never silently destroy analyst state" is violated in several editors** — dirty dashboard /
  Knowledge / Contract drafts vanish on any in-app navigation (no `useBlocker`) [NEW F-3].
- **Noise control — the market's #1 need — is the least-built area.** The learning loop
  (triage → detection tuning), flaky-check detection, snooze/maintenance windows, and noise
  analytics are all still open [tracked #31/#61/#62/#248], and **errored (broken) checks are
  visually indistinguishable from genuinely-failing checks** across every surface [tracked #262 +
  LIVE], polluting the very worklist the excellent triage UX depends on.

---

## 3. Market research — what the intended users actually need in 2026

Sources are listed in §8. Signal is consistent across the Monte Carlo *State of Data Quality*,
the 2026 Gartner *Market Guide for Data Observability*, the DataKitchen OSS landscape, and
practitioner writing.

**The needs, ranked by how loudly the market states them:**

1. **Alert fatigue / false positives is the #1 complaint.** Tools "stop at detection and exhaust
   teams with alert fatigue." If 48 of 50 alerts don't matter, all 50 get ignored. Top noise
   sources named: benign schema-order changes, weekend volume dips, transient upstream outages.
   What the market rewards: **false-positive feedback that tunes detection** (Elementary), noise
   suppression once RCA is found, and **routing to owners** — "alerts without clear owners are
   noise."
2. **Detection and resolution are slow, and the business notices first.** Monte Carlo's survey:
   **68%** take **4+ hours to detect**; average **~15 hours to resolve** (+166% YoY); **~67
   incidents/month**; **74%** of the time **business stakeholders find the issue first**; **~31%**
   of revenue exposed; teams maintain **~290 hand-written tests**.
3. **Manual rule authoring doesn't scale** — the core OSS complaint (GX/Soda/dbt): labor-intensive,
   incomplete coverage, steep learning curve, needs DevOps bandwidth. **Auto-generation of checks**
   is the single most-named differentiator; hand-written tests are called "as obsolete as
   hand-coding HTML" for AI-scale data.
4. **Data quality is now the #1 blocker to AI.** Gartner: **~60% of AI projects abandoned through
   2026** due to insufficient data quality; **75%** of leaders don't trust their data; **data trust
   is the #1 AI-adoption concern**. AI workloads are the **top driver** of observability adoption;
   semantic/embedding-drift monitoring is emerging.
5. **Shift-left: contracts + pipeline/CI gates, with detection as a loop.** The 2026 default is
   prevention (contracts + quality gates that **block bad data at merge/deploy**) *plus* detection
   feeding back. The pointed critique: *"most data-contract tools don't enforce contracts —
   enforcement is the differentiator."* Only **32%** of orgs have granular governance.
6. **Agentic RCA and guarded remediation.** **23%** of teams already use agentic RCA/remediation,
   **+38%** plan to; Gartner names **ML anomaly detection + automated RCA + automated remediation**
   as the capability set. The consensus shape is a **human-in-the-loop co-pilot with guardrails**,
   never an autonomous source-writer. Market: **$346.4M, +20.8%** YoY.
7. **Triage discipline** — the "trinity of triage": *is it real / who's impacted / what caused it*,
   backed by incident lifecycle + status + assignee + ownership and lineage-driven impact.
8. **TCO anxiety** — enterprise SaaS runs **$100k+/yr** with consumption-pricing escalation risk;
   buyers now model TCO explicitly. **This is the wedge self-host/OSS is built for.**

### 3.1 DQ Sentinel scored against those needs

| # | Market need | DQ Sentinel | Gap |
|---|---|---|---|
| 1 | **Reduce noise / false positives** | 🟡 Partial | Has fingerprint dedup, recurrence, auto-resolve, expected→KB. **Missing the learning loop** (#31/#62), flaky detection + maintenance windows (#61), noise analytics (#248); drift false-fires (#265/#270); **broken≠failing not separated** (#262). **Biggest strategic gap.** |
| 2 | **Detect/resolve fast** | 🟡 Partial | RCA + incident lifecycle + MTTR exist, but RCA is **on-demand only**, scheduling is cron (not continuous), MTTD/MTTR is blank until history. |
| 3 | **Auto-generate checks** | ✅ **Strength** | Heuristic + LLM check-gen + **exploration agent** + monitor packs directly answer the #1 OSS complaint. Caveat: heuristic proposals normalize observed dirt (#256). |
| 4 | **DQ for the AI stack** | ✅ **Differentiated** | Provider-agnostic incl. **local models** + MCP = a data-residency AI story no competitor matches. Missing: monitoring the AI/unstructured data itself. |
| 5 | **Prevention: contracts + gates** | 🟡 **Half** | ODCS contracts **with enforcement** already ship (ahead of most). **Missing the hot half: CI/PR gating + data-diff** (#221/#222/#229). |
| 6 | **Agentic RCA / remediation** | ✅ Ahead (OSS) | RCA agent beats the OSS field, but its **structured report is dead in the UI**, remediation is design-only (#227), auto-trigger absent. |
| 7 | **Triage workflow** | ✅ **Best-in-class** | The strongest market fit in the whole product. Ownership is free-text, not wired to routing. |
| 8 | **TCO / residency** | ✅ Win | OSS self-host + optional local AI. **But procurement is blocked** by no SSO (#26), no DSN encryption (#24), no per-connection RBAC/tenancy (#72). |

**One-line evaluation:** *DQ Sentinel is strongest exactly where the market is loudest about
**workflow** (triage) and **authoring** (AI check-gen), and its self-host + local-AI story uniquely
answers both **TCO** and **AI-data-trust**. It is weakest exactly where the market is heading
**next**: noise reduction as a learning system, prevention via CI/pipeline gates, and closing the
detection→tuning loop. The near-term reputational risk is that **noise undercuts the excellent
triage UX**, and two flagship AI surfaces ship consumers for contracts the backend doesn't
fulfill.*

---

## 4. Ten recommendations — how the system should EVOLVE (strategic)

These are product-direction moves, ordered by leverage. They lean on the market evaluation (§3)
and reuse machinery that already exists.

**A1 · Make noise reduction a first-class learning system.** This is the #1 market need and the
product's biggest strategic gap. Ship the closed loop: **triage feedback → automatic threshold /
flaky-check tuning** (#62 + #31), **snooze-with-expiry + maintenance windows** (#61), and a
**noise-funnel analytics** view (alerts → real → actioned) (#248). Split the today-collapsed
signals: *"expected"* is **knowledge** (feeds the KB), *"false positive"* is **detection tuning**
(feeds thresholds) — they must do different things. **This single evolution protects everything
else the product does well.**

**A2 · Separate "broken" from "failing" everywhere.** An errored check (source unreachable, bad
config, infra) is an **operational** problem; a failing check is a **data-quality** problem. Today
they render identically across Home, Checks, Incidents, and My-work [#262 + LIVE], so the analyst's
worklist is polluted with things they can't triage. Introduce a distinct **"needs fixing" lane**
(check health) separate from **"needs triage"** (data health). Cheap, high-impact, and it directly
defends the crown-jewel triage UX.

**A3 · Close the prevention gap: ship the CI / pipeline quality gate + data-diff.** This is the
market's hottest 2026 move (shift-left, "contracts that *enforce*"). DQ Sentinel already has
contracts, enforcement, and (planned) checks-as-code — add the **gate** so checks run at
**merge/deploy time** and can block bad data before it propagates (#221 gates, #222 gitops, #229
service tokens as the keystone). This converts a detection tool into a **prevention** tool and
opens the data-engineer persona.

**A4 · Make RCA proactive and structured.** Agentic RCA is where the product leads OSS — finish the
job: **auto-trigger RCA on failure and attach the report to the incident**, and **complete the
structured evidence contract** (#60) so the already-built hypotheses/evidence/confidence UI comes
alive (see B3). This matches Gartner's "automated RCA" capability and the 23%→61% agentic-adoption
curve, while staying human-in-the-loop.

**A5 · Turn ownership into a routing primitive, not a text field.** The market is emphatic:
"alerts without owners are noise," and business finds issues first 74% of the time. Wire
dataset/incident **owner → notification routing + on-call + scheduled digests** (#42/#61). The
fan-out already exists; make *who owns it* decide *who gets paged* and *who gets the daily digest*.

**A6 · Offer a "recommended coverage" onboarding without abandoning human-in-the-loop.** Answer the
#1 OSS complaint (manual authoring doesn't scale) by having onboarding **auto-propose a starter
monitor pack per dataset** from the profile, which the analyst **approves** in bulk — leveraging the
already-shipped monitor packs + check-gen. This gets the Anomalo-style "coverage in minutes"
outcome while keeping the product's "profile → recommend → human approves" principle (and avoiding
the "monitor everything" noise/cost the design philosophy rightly rejects).

**A7 · Give stakeholders a self-serve trust surface.** Extend the already-good read-only Status page
into **dataset subscriptions + embeddable status badges** (#223/#244) so consumers can subscribe to
the tables they depend on and embed a freshness/health badge in dashboards and READMEs. This attacks
the "business finds out first" statistic head-on.

**A8 · Unblock enterprise procurement.** The self-host + local-AI TCO story is wasted if a buyer's
security review fails on table-stakes. Prioritize **SSO/OIDC** (#26), **DSN encryption at rest**
(#24), and a **per-connection RBAC UI + tenancy scoping** (#72). These are the named gaps in the
competitive analysis and the difference between "great tool" and "procurable product."

**A9 · Own the "data quality for the AI stack" position.** Gartner makes DQ the #1 AI blocker and
names data residency as a differentiator — a position DQ Sentinel is *uniquely* built for
(provider-agnostic + local models + MCP). Lean into it: **NL→check authoring in the assistant**
(#71 is adjacent), and explore **semantic/embedding-drift** monitoring as a first-class check to
cover the AI-data use case the whole market is converging on.

**A10 · Restore design restraint before adding more surfaces.** "Best-in-class UX" for an all-day
tool means the product **stops accreting nav items**. Re-consolidate to the philosophy's target
(cross-dataset-spanning concerns at top level; everything else into dataset tabs/drawers), land the
**v2 three-theme reskin coherently** (#174), and treat the ≤3-group / ≤12-item rule as a shipping
gate. A sharp, calm information architecture is itself a competitive feature against the bloated
enterprise consoles.

---

## 5. End-to-end audit — findings (UX, features, best-practice, half-baked)

This is the deep-dive. Everything below is **verified against current code** and **de-duplicated**
against the tracker. Priority: **P0** ship-blocker · **P1** serious · **P2** notable · **P3** polish.

### 5.1 Security & data-isolation

| ID | P | Finding | Evidence | Status |
|---|---|---|---|---|
| S1 | **P1** | **Four source-data routers bypass the per-connection grant model.** `saved_queries`, `adhoc_dashboards`, `custom_dashboards`, `lineage` never call `assert_connection_*` — viewer-reachable execution of persisted SQL / DDL introspection against **any** connection by id. Cross-tenant data + schema exfiltration through the exact control `/query/run` was hardened with. | `api/saved_queries.py:181`, `api/adhoc_dashboards.py`, `api/custom_dashboards.py:141`, `api/lineage.py:26-97` | **[NEW]** — outside #72's named list |
| S2 | **P1** | **`guard_sql` denylist misses read-side file/network table functions.** DuckDB `read_text` (#267) is one instance of a class: MySQL `LOAD_FILE`, ClickHouse `url()/s3()/file()` all pass the guard → arbitrary server-file read + outbound SSRF from any check/workbench/LLM query. | `connectors/safety.py:15-19` | **[NEW]** generalizes #267 |
| S3 | P2 | API published on host `:8000` bypassing nginx, exposing unauthenticated `/metrics`, in a stack that defaults `DQ_ENV=prod`. | `docker-compose.yml:34,54-55` | **[NEW]** |
| S4 | P3 | `get_current_user` reads `int(payload["sub"])` inside a block catching only `PyJWTError` → a signed token with bad `sub` yields an uncaught **500 instead of 401**. | `security.py:52` | **[NEW]** (REST twin of the tracked chat-WS case) |
| S5 | P3 | LIKE-wildcard injection (tracked for exceptions `q`) **recurs** in other list filters — `%`/`_` are unescaped wildcards. | `saved_queries.py:65`, `datasets.py:43` | **[NEW]** (same root as #273) |

### 5.2 Correctness, state, and "never destroy analyst work"

| ID | P | Finding | Evidence | Status |
|---|---|---|---|---|
| C1 | **P1** | **Contract column editor loses focus every keystroke.** Row `key` includes `col.name`, which the row's own input edits → React remounts the row per character. Hand-adding contract columns is effectively impossible. | `pages/dataset/ContractTab.tsx:466` | **[NEW]** |
| C2 | **P1** | **Dirty editors aren't guarded against in-app navigation.** No `useBlocker` anywhere; dashboard-builder / Knowledge / Contract drafts are discarded on any sidebar/topnav/search click (only `beforeunload` covers tab-close). Directly violates the standing bar. | `pages/CustomDashboardPage.tsx:93-117`, `pages/DatasetDetailPage.tsx:39-52` | **[NEW]** |
| C3 | **P1** | **Workbench suggestion Run/Edit silently overwrites unsaved SQL** — bypasses the `confirmReplace()` guard the same page uses for saved-query/history loads. | `pages/WorkbenchPage.tsx:517-526` | **[NEW]** |
| C4 | P2 | **Check mutations don't invalidate dataset queries** → header shows "3 active checks" while the table below shows 4, in one viewport. Triage + monitor packs already invalidate `qk.datasets`; checks don't. | `components/ChecksTable.tsx:150-154`, `pages/dataset/ChecksTab.tsx:42-45` | **[NEW]** |
| C5 | P2 | **Bulk-triage selection persists after the action**, re-targeting now-invisible rows in the next step — a mis-triage generator. | `components/exceptions/ExceptionsWorkspace.tsx:147-162` | **[NEW]** (distinct from the tracked #212 undo gap) |
| C6 | P2 | **Contract conformance returns a raw HTTP 500** when the source is unreachable, while peer endpoints (`/query/run`, Runs) degrade with a clean message. No global exception handler. | `core/contracts.py:590`, `api/contracts.py:266` | **[LIVE]** [NEW] |
| C7 | P2 | **CSV export failure is completely silent** — `try/finally` with no `catch`; 403/500/network rejections vanish while the button returns to idle. | `components/exceptions/FilterBar.tsx:45-53` | **[NEW]** |
| C8 | P3 | **PanelChart blanket-`reverse()`s rows** assuming DESC input → any `ORDER BY ASC` chart renders time backwards, inverting trend readings. | `components/PanelChart.tsx:94-96` | **[NEW]** |
| C9 | P3 | **localStorage isn't namespaced per user** — saved views, landing page, worksheet tabs, and **full SQL history** carry across logins on a shared VDI (the file's own stated enterprise scenario). | `lib/prefs.ts`, `lib/queryHistory.ts`, `lib/workbenchTabs.ts` | **[NEW]** |

### 5.3 Half-baked features (built one side only)

| ID | P | Finding | Evidence | Status |
|---|---|---|---|---|
| H1 | **P2** | **Structured RCA report is dead.** Backend `RcaOut` exposes only `report_md` + `root_cause_summary` (the agent's `confidence`/`suggested_fixes` are dropped); frontend requires `report_json: RcaReport` → `RcaReport.tsx:307` reads it always-null → the whole hypotheses/evidence/confidence UI + `rcaExport` is unreachable. The `"tool"` transcript step the loop emits isn't in the TS union either, so tool steps drop. A flagship differentiator ships a **lying type contract**. | `schemas.py:896-904`, `llm/rca_agent.py:35-40`, `api/types.ts:543-560`, `components/RcaReport.tsx:256-307` | **[NEW/verified]** (backend half is #60) |
| H2 | P2 | **Per-user connection grants have zero UI** — `GET/POST/DELETE /auth/users/{id}/grants` are never called; Home is "grant-scoped" but admins can only manage scoping via curl. | `api/auth.py:117-170`, no caller in `frontend/` | **[NEW]** (verify vs #72) |
| H3 | P2 | **No way to unregister a dataset from the UI** — `DELETE /datasets/{id}` exists, never called; a mis-registration pollutes health rollups/coverage forever unless an admin deletes the whole connection. | `api/datasets.py:298`, no caller | **[NEW]** |
| H4 | P2 | **Custom-dashboard SQL widgets never auto-refresh** — snapshots update only on manual save/open; a "dashboard" silently serves stale data in a product whose entire point is freshness. | `api/custom_dashboards.py:137-140` (`TODO(#42)`) | **[NEW]** (worker half is #42) |
| H5 | P3 | Endpoints shipped without their management UI: **SLA edit** (`PATCH /sla/{id}`), **saved-query rename** (`PATCH /queries/{id}`), **contract delete** (`DELETE …/contract/{id}`) — all forcing delete-and-recreate. | never-called API sweep | **[NEW]** |
| H6 | P3 | **"Filtered by scorecard" strip has no producer** — nothing creates `?domain=`/`?team=` links; `GET /scorecards/rollups`/`/datasets` are never called. Shipped+unit-tested code for a drill-in that can't be started. | `components/datasets/RollupFilterStrip.tsx` | **[NEW]** |
| H7 | P3 | **Home "MTTD / MTTR" KPI is a hardcoded `"—"`** placeholder while `mttr_seconds` exists on `/sla/reliability`. A dead tile on the flagship overview reads as "metrics broken." | `pages/HomePage.tsx:144-148` | **[NEW]** (matches LIVE walkthrough) |

### 5.4 Engineering practice — tests, CI, reproducibility, ops

| ID | P | Finding | Evidence | Status |
|---|---|---|---|---|
| E1 | **P1** | **Builds are not reproducible** — no lockfile/constraints anywhere; images and CI resolve `>=` floors at build time, so two `--build` runs days apart differ and a bad upstream release breaks prod rebuilds silently. (Frontend has `package-lock.json`; backend has nothing.) | `backend/pyproject.toml`, `backend/Dockerfile:8` | **[NEW]** |
| E2 | **P1** | **Migrations are only ever tested on SQLite** while prod is Postgres and `init_db()` runs `upgrade head` at boot; the drift guard compares **names only** (not types/nullability/FK), and no test calls `downgrade()`. A PG-only migration failure ships untested and bricks api+worker startup. | `tests/test_migrations.py:21-56`, `migrations/env.py`, `ci.yml:19` | **[NEW]** |
| E3 | P2 | **CI never builds the Docker images or boots the stack** (only `compose config`); the 39-assertion `e2e_smoke.py` runs manually only. Broken Dockerfile/nginx merges green. | `.github/workflows/ci.yml:24-25` | **[NEW]** |
| E4 | P2 | **Containers run as root; no healthchecks / restart-detection / resource bounds** on api/worker/frontend (only postgres has a healthcheck). A wedged worker stays "Up" forever and silently stops running checks. | `docker-compose.yml:26-79`, both Dockerfiles | **[NEW]** |
| E5 | P2 | **No dependency-vulnerability automation** for an OSS launch — no `dependabot.yml`, no `pip-audit`/`npm audit`, no CodeQL. | `.github/` listing | **[NEW]** |
| E6 | P2 | **The analyst-critical flows have zero component tests.** Frontend coverage is concentrated in pure helpers; the exceptions workspace, `CheckParamsForm`, and Workbench (exactly where the 2026-07 UX P1/P2s clustered) have no RTL tests; the two LLM agent loops (`explorer`, `rca_agent`) and `check_gen`'s parse path have no backend tests. | 16 FE test files vs ~40 components; no `test_rca/explorer/check_gen.py` | **[NEW]** |
| E7 | P3 | **The `#156` optimistic-concurrency guard is dead in production** — backend implements + tests 409-on-stale, but the UI never sends `expected_versions`, so concurrent analysts silently clobber each other. Passing backend tests give false confidence. | `ExceptionsWorkspace.tsx:149`, `DetailPanel.tsx:88` | **[tracked #166]** — confirmed still-live |
| E8 | P3 | **Test isolation is naming-discipline only** (single session-scoped app DB; `unique=True` connection names); a few fixed literals + cleanup-order coupling block `pytest-xdist`. | `tests/conftest.py:13-91` | **[NEW]** |
| E9 | P3 | **README/AGENTS/docs drift** — README lists the *shipped* audit log as a known gap and shows 11 of 14 check types; `AGENTS.md` says the dataset page has 9 tabs (it has 12) and that the admin password changes "via Settings" (no such UI); CHANGELOG `[Unreleased]` is empty despite #276–#280. | `README.md`, `AGENTS.md:58,114` | **[NEW]** |

### 5.5 Accessibility

| ID | P | Finding | Evidence | Status |
|---|---|---|---|---|
| X1 | P2 | **Assistant stream has no ARIA live region** — streaming status, steps, and appended messages are visual-only; the flagship AI surface is silent to screen readers mid-turn (bulk-triage progress *does* have one, so the pattern exists). | `pages/AssistantPage.tsx:212-232` | **[NEW]** |
| X2 | P2 | **Global ⌘K search lacks combobox/listbox semantics** — no `role="combobox"`, `aria-expanded/controls/activedescendant`; the active hit is styled only, result count never announced. In the app's primary navigation device. | `components/Layout.tsx:225-291` | **[NEW]** |
| X3 | P3 | **Absolute timestamps are unlabeled local time without a year**; `DocsPage` bypasses the central `parseUtc`. Distributed teams reconciling against UTC warehouse logs mis-read by their offset. | `lib/format.ts:24-33`, `pages/DocsPage.tsx:10-13` | **[NEW]** |

### 5.6 What is already good (do not regress)

A balanced audit records the strengths, because several are genuinely ahead of the field:

- **Exceptions triage workspace** — server paging, live facets, saved views with absolute counts,
  keyboard triage, bulk ops, CSV, concurrency toasts. Commercial-grade.
- **Authz negative-path testing is exemplary** — viewer-cannot-mutate at both global-role and
  per-grant level, 404-indistinguishability probes, grant-never-elevates.
- **Safety guard, WS chat, and prod-config guards are tested to a high bar** (adversarial SQL
  literals/comments/dollar-quotes; a test proving the default secret enables token forgery).
- **Migration chain is linear with full downgrades**, a tested pre-Alembic stamp path, and a
  Postgres advisory lock for concurrent api/worker boot.
- **N+1 budgets are pinned** after #280; **all frontend deps are actually used**; **zero
  `as any`/`@ts-ignore`** in the frontend; `Modal` has a real focus trap + restore; `styles.css`
  has systematic `:focus-visible` + `prefers-reduced-motion`.
- **LLM-off degradation** is honest and actionable everywhere; **CONTRIBUTING/SECURITY/LICENSE/
  CHANGELOG + issue/PR templates** are present and accurate.

---

## 6. Ten recommendations — concrete UPDATES we need (from the audit)

Ordered by severity × reach. Each is a specific, shippable change with the evidence above. Nine are
focused; #10 bundles the small verified defects into one polish sweep.

**B1 · Scope the four unscoped source-data routers (P1 security).** Thread
`assert_connection_role(...,"editor")` (execute paths) / `assert_connection_visible` (lineage/open
reads) through `saved_queries`, `adhoc_dashboards`, `custom_dashboards`, and `lineage`, and scope
their list/get queries with `visible_connection_ids`. Closes a viewer-reachable cross-tenant
exfiltration path [S1]. File as a new issue *(outside #72's checklist)*.

**B2 · Harden `guard_sql` against read-side file/network functions (P1 security).** Move from a
write-oriented denylist to a **sqlglot-parsed allowlist** that rejects unknown table-valued
functions (or, minimum, add the per-dialect `read_text/read_csv/read_blob/read_parquet/LOAD_FILE/
url/s3/file` names). Fixes the whole class that #267 is one instance of [S2].

**B3 · Revive or retire the structured RCA report (P2, flagship honesty).** Either **emit
`report_json` from `rca_agent` and add it to `RcaOut`** (this is the frontend for #60, already
built and waiting), or **delete the dead `RcaReport` structured UI + the lying type**. Add the
`"tool"` (+`name`) step to `TranscriptStep` either way so tool steps stop dropping [H1]. Pairs with
strategic A4.

**B4 · Make check mutations invalidate dataset queries (P2, trust).** Add `qk.datasets.all` +
`qk.datasets.detail(datasetId)` to the shared `invalidate()` used by activate/dismiss/archive/
run-now (and `ContractTab.activate`). Kills the "header says 3, table shows 4" self-contradiction
[C4].

**B5 · Stop destroying dirty editor state (P1, standing bar).** Wire a react-router v7 `useBlocker`
to the existing `dirty` flags on the dashboard builder, Knowledge, and Contract tabs [C2], route
the Workbench suggestion Run/Edit through `confirmReplace` [C3], and fix the Contract column-row
`key` so the editor stops eating focus [C1]. Three fixes, one theme: never lose analyst work.

**B6 · Make builds reproducible and test migrations on Postgres (P1, ops).** Commit a backend lock
(uv/pip-tools export) consumed by the Dockerfile + CI [E1]; add a CI job with a `postgres` service
running `upgrade head → downgrade base → upgrade head` and extend the drift guard to compare
types/nullability/uniques via `compare_metadata` [E2]. For a self-hosted product, the operator's
build *is* the product.

**B7 · Harden the container/compose posture and exercise it in CI (P2, ops/security).** Add a
non-root `USER` to the backend image and an unprivileged nginx base [E4]; add health-based
healthchecks + `depends_on: service_healthy` + worker `mem_limit`/`cpus` [E4]; drop or
loopback-bind the host `:8000` mapping and keep `/metrics` internal [S3]; and add a CI job that
`docker build`s both images and runs `e2e_smoke.py` [E3]. Add `dependabot.yml` + a `pip-audit`/
`npm audit` step for the OSS launch [E5].

**B8 · Ship the missing management UIs for endpoints that already exist (P2, half-baked).** A
**per-user connection-grants editor** in the Settings user table [H2]; **"Unregister dataset"**
(typed-confirm, cascade spelled out) on the dataset header [H3]; and **SLA edit** / **saved-query
rename** / **contract delete** so none of them force delete-and-recreate [H5]. Every one of these is
a wired backend endpoint with no button.

**B9 · Make every failure path degrade honestly (P2, correctness).** Add a **global exception
handler** returning a clean error shape with the request-id, and catch source-connection errors in
contract conformance so it stops returning a raw 500 [C6]; **catch and toast** CSV-export failures
[C7]; broaden `get_current_user` to return 401 (not 500) on a malformed `sub` [S4]. One "no bare
500s, no silent failures" sweep.

**B10 · Polish sweep (bundled P2/P3 verified defects).** (a) Separate errored-check state from
data-quality-failure state at the data layer so #262's noise stops [A2 groundwork]; (b) a shared
`escape_like()` for every `q=` filter [S5]; (c) fix `PanelChart`'s trend-inverting `reverse()`
[C8]; (d) namespace `localStorage` per user or clear on logout [C9]; (e) prune bulk-selection to
still-visible rows after triage [C5]; (f) compute or remove the hardcoded MTTD/MTTR tile [H7];
(g) add `aria-live` to the assistant stream and combobox ARIA to ⌘K search [X1/X2]; (h) label
absolute timestamps with a UTC cue + year [X3]; (i) add the first RTL/component tests for the
triage + Workbench flows [E6]; (j) reconcile README/AGENTS/CHANGELOG with what actually shipped
[E9].

---

## 7. Priority sequencing

| Wave | Do first | Why |
|---|---|---|
| **Now (P1 safety/correctness)** | B1, B2 (security) · B5, B4 (analyst-state/trust) · B6 (reproducibility) | Cross-tenant exfiltration, arbitrary file read, silent work-loss, and non-reproducible builds are the items that turn "great demo" into "incident." |
| **Next (strategic wedges)** | A1 + A2 (noise) · A3 (gates) · B3 + A4 (RCA) | The market's #1 need and its next frontier; also where two flagship AI surfaces are currently under-delivering. |
| **Then (procurement + reach)** | A8 (SSO/encryption/RBAC) · A5 + A7 (ownership/subscriptions) · B7, B8 | Unblocks the enterprise buyer and the stakeholder persona; finishes the half-built management surfaces. |
| **Ongoing polish** | B9, B10 · A6, A9, A10 | Honest degradation, accessibility, coverage onboarding, and IA restraint. |

---

## 8. Sources (market research)

- Monte Carlo — [The State of Data Quality survey](https://montecarlo.ai/blog-data-quality-survey) ·
  [2026 Gartner Market Guide, annotated](https://montecarlo.ai/blog-what-2026-gartner-market-guide-for-data-observability-tools-means-for-your-data-and-ai-team-my-take)
- Atlan — [Data Quality Alerts: reducing fatigue](https://atlan.com/know/data-quality-alerts/) ·
  [Data contracts in 2026](https://atlan.com/data-contracts/)
- DataKitchen — [2026 Open-Source DQ & Observability Landscape](https://datakitchen.io/blog/the-2026-open-source-data-quality-and-data-observability-landscape/)
- Ataccama — [The Shift-Left Playbook: contracts, gates, feedback loops](https://www.ataccama.com/blog/the-shift-left-playbook-data-contracts-data-quality-gates-and-feedback-loops)
- Hex — [State of Data Teams 2026](https://hex.tech/state-of-data-teams/) ·
  Dremio — [Agentic analytics a top 2026 priority](https://www.dremio.com/press-releases/agentic-analytics-and-ai-driven-decision-making-are-top-priorities-for-2026-according-to-new-survey/)
- Rootly — [AI observability 2026: predictive alerts & automated fixes](https://rootly.com/sre/ai-observability-2026-predictive-alerts-automated-fixes) ·
  IBM — [Observability trends 2026](https://www.ibm.com/think/insights/observability-trends)
- G2 — [Bigeye vs Monte Carlo](https://www.g2.com/compare/bigeye-vs-monte-carlo) ·
  Medium — [Alert fatigue in DataOps](https://medium.com/@manik.ruet08/alert-fatigue-in-dataops-how-to-build-a-smarter-alerting-system-0e70750fb9cf)

---

## 9. Tracker filing (2026-07-16)

The untracked findings were filed on the **DQ Sentinel** project board (#4). Already-tracked
findings are referenced, not re-filed.

| Issue | Findings | Lane |
|---|---|---|
| [#281](https://github.com/k-sandhu/dq-sentinel/issues/281) | S2 — guard_sql read-side file/network functions | platform |
| [#282](https://github.com/k-sandhu/dq-sentinel/issues/282) | C6/S4/S5 — global exception handler, 401-not-500, escape_like | platform |
| [#283](https://github.com/k-sandhu/dq-sentinel/issues/283) | C1 — contract editor focus loss | polish |
| [#284](https://github.com/k-sandhu/dq-sentinel/issues/284) | C2/C3 — dirty-nav guard + workbench SQL clobber | polish |
| [#285](https://github.com/k-sandhu/dq-sentinel/issues/285) | C4 — check-mutation cache invalidation | polish |
| [#286](https://github.com/k-sandhu/dq-sentinel/issues/286) | C5 — bulk stale selection | polish |
| [#287](https://github.com/k-sandhu/dq-sentinel/issues/287) | H1 — dead structured RCA report | rca |
| [#288](https://github.com/k-sandhu/dq-sentinel/issues/288) | H2 — per-user grants UI | platform |
| [#289](https://github.com/k-sandhu/dq-sentinel/issues/289) | H3/H5 — missing management UIs | polish |
| [#290](https://github.com/k-sandhu/dq-sentinel/issues/290) | E1 — reproducible builds / lockfile | platform |
| [#291](https://github.com/k-sandhu/dq-sentinel/issues/291) | E2 — Postgres migration testing | platform |
| [#292](https://github.com/k-sandhu/dq-sentinel/issues/292) | E3/E4/S3/E5 — Docker CI + container hardening | platform |
| [#293](https://github.com/k-sandhu/dq-sentinel/issues/293) | E6/E8 — test coverage + isolation | polish |
| [#294](https://github.com/k-sandhu/dq-sentinel/issues/294) | C7/C8/C9/H6/H7/X1/X2/X3 — frontend polish sprint | polish |
| [#295](https://github.com/k-sandhu/dq-sentinel/issues/295) | E9 — docs drift | polish |
| [#72 comment](https://github.com/k-sandhu/dq-sentinel/issues/72#issuecomment-4996232954) | S1 — four unscoped source-data routers | platform |

**Referenced, not re-filed:** #42 (H4 dashboard auto-refresh — the code TODO already points here),
#166 (E7 optimistic-concurrency guard dead in prod), #214/#216 (frontend-audit lane), #60 (RCA
backend contract), #209 (session-expiry state loss), #212 (bulk undo), #262/#31/#61/#62/#248
(noise / errored-check strategic theme → §4 A1/A2).

---

> *Internal evidence base: parallel audits of `backend/app/**`, `frontend/src/**`,
> `.github/workflows/ci.yml`, `docker-compose.yml`, Alembic migrations, and a live click-through of
> the standing demo (UI :3002 / API :18002). De-duplicated against `docs/BROKEN-FLOWS.md`,
> `docs/competitive-analysis.md`, `docs/codebase-deep-dive-action-plan.md`, the 2026-07 UX
> benchmark, and open issues #24–#275.*
