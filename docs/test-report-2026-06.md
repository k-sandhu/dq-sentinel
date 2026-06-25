# DQ Sentinel — Platform Test Report (June 2026)

Date: 2026-06-25 · Scope: backend end-to-end, frontend UI/UX, and LLM features.

> **What this is.** A faithful record of a full manual + scripted test pass over DQ Sentinel:
> the Python backend (unit + live end-to-end), the React frontend (build + driven UI/UX), and
> the LLM-native features against a **real provider** (OpenRouter). Every number below was
> observed in this run; LLM outputs were independently re-checked against the source data
> (see [Appendix: ground-truth verification](#appendix-ground-truth-verification)).

## Environment

| Item | Value |
|---|---|
| Backend | FastAPI via `uvicorn`, app DB = SQLite (`dqsentinel_e2e.db`) |
| Source data | `samples/shopdb.sqlite` (synthetic shop DB with seeded DQ issues) |
| Frontend | Vite dev server (`:5173`) proxying `/api` → `:8000` |
| LLM provider | OpenRouter (OpenAI-compatible path), model `anthropic/claude-haiku-4.5` |
| LLM key | `OPENROUTER_API_KEY` read from the host environment (consumed via the `llm_api_key` alias); never written to disk or committed |

## Summary

| Area | Result |
|---|---|
| Backend unit suite (`pytest`) | ✅ 363 passed, 1 skipped |
| Backend E2E smoke (`scripts/e2e_smoke.py`) | ✅ 38/38 checks |
| Backend supplementary live probe (extra routers) | ✅ all behaviors pass |
| Frontend typecheck / unit (`vitest`) / build | ✅ clean / 23 passed / build OK |
| Frontend UI/UX tour | ✅ zero console errors; design system on-spec |
| LLM: query suggestions | ✅ engaged, grounded |
| LLM: ad-hoc dashboard | ✅ engaged, grounded |
| LLM: check generation (+ exploration agent) | ✅ engaged, grounded |
| LLM: assistant chat (WebSocket, tool-use) | ✅ engaged, grounded |
| LLM: root-cause-analysis (RCA) agent | ⚠️ **fails to converge** — see [Findings](#findings--recommendations) |

---

## Backend

**Unit suite** — `cd backend && pytest`: **363 passed, 1 skipped** (~33s). No failures.

**End-to-end smoke** — `scripts/e2e_smoke.py` against a live server + the seeded sample DB:
**all 38 checks passed**. It exercises the full workflow: login → connection → introspection
→ register datasets → profile → knowledge → heuristic check generation → activate/run →
custom SQL check → **write-guard rejection (`422`)** → ML outlier detection → exception triage
→ workbench → ad-hoc dashboard → fleet health → dashboard aggregates → RCA `503` fallback.
Highlights that confirm correct behavior: freshness check **fails** at 34.5h vs a 24h SLA,
future-dated rows are excluded, the custom SQL check catches 589 total/line-item mismatches,
ML flags the planted 100× payment outliers, and `DELETE`/`DROP` SQL is rejected at creation
and in the workbench.

**Supplementary live probe** — exercised routers the smoke test skips (insights, scorecards,
SLA, search, lineage, incidents, audit, in-app docs, saved queries, custom dashboards,
contracts, MCP servers) plus auth boundaries (`401` on unauthenticated read/write) and
`/metrics`. All passed. (OpenAPI is intentionally served at `/api/v1/openapi.json`.)

LLM-gated features correctly degraded to heuristics / `503` when the run was done with the
key cleared, satisfying the "degrade gracefully" golden rule.

## Frontend

`tsc --noEmit` clean · `vitest` **23 passed** · `vite build` succeeds.

> Note: a one-time fix `npm install @rollup/rollup-win32-x64-msvc --no-save` was needed (the
> known npm optional-deps bug, documented in AGENTS.md). Do not add it to `package.json`.

UI/UX tour (driven live, **zero console errors on every page**):

- **Design system on-spec**: brand `#509ee3`, light bg `#f9fbfc`, dark bg `#14181d` with a
  layered card surface; theme follows OS `prefers-color-scheme` and the brand even shifts to a
  brighter `#5ba3e8` for dark-mode contrast.
- **Overview**: KPI cards, results chart, 90-day incident heatmap with per-day ARIA labels,
  graceful empty states, "Skip to main content" link.
- **Datasets / dataset detail**: health-filter chips, favorite stars with descriptive ARIA,
  formatted numbers, relative timestamps; rich per-column profile (null %, distinct, μ/σ,
  ranges, top values); 12-tab detail page.
- **Exceptions triage** (core analyst flow): filter rail + bulk actions; acknowledging a row
  fired `POST /exceptions/triage` and optimistically updated counts (open 147→146).
- **Workbench**: CodeMirror editor + schema browser; a run returned a result grid
  ("50 rows · 104 ms") with CSV/JSON/Chart export.
- **Global search** (Ctrl-K), **Settings**, **Reliability** (SLA) all clean.
- **Responsive**: at 375px no page-level horizontal scroll; navigation stays reachable.

## LLM features

Run against OpenRouter with `anthropic/claude-haiku-4.5`. `/api/v1/health` reported
`llm_enabled: true`. Four of five features work and produce **grounded, correct** output
(numbers cross-checked in the appendix). The RCA agent is the exception.

### ✅ Query suggestions (`mode: llm`)
7 data-aware suggestions, **all 7 runnable**, e.g. "Negative and zero amounts",
"Future-dated orders", "Amount outliers by status" — matching the actual seeded issues.

### ✅ Ad-hoc dashboard (`origin: llm`)
8 panels, **all executed without error**: total revenue, order volume & revenue by status,
daily trends, negative/zero-amount anomalies, average order value by country, and an amount
percentile panel that surfaces the `MIN = −28,330`. Title and panels are coherent and grounded.

### ✅ Check generation + exploration agent (`mode: llm`)
On `customers`, the agent proposed **10 checks** that directly target the seeded issues: a
PK uniqueness + not-null pair, an email regex (flagging `not-an-email` / `a@b`), an email
duplicate `custom_sql`, a country domain that explicitly notes the lowercase `us` casing bug,
a `loyalty_tier` domain, a binary `marketing_opt_in`, a `signup_date` range catching future
dates, and `full_name` not-null/length. Rationales cite real profile statistics.

### ✅ Assistant chat (WebSocket, tool-use streaming)
Streamed `thinking → 3 tool calls (get_dataset_overview ×3) → answer`. The final answer
correctly identified that `orders` has the most open exceptions (97) and that the
`total_amount` vs line-items mismatch is the dominant issue — grounded in tool output, not
guessed.

### ⚠️ Root-cause analysis (RCA) agent — does not converge
The RCA agent performs **excellent** investigation — across runs it independently found 590
total/line-item mismatches, that **131 of 222 negative orders are exact sign errors**
(`total_amount = −Σ line_total`), 592 orphan `order_id`s, and the `Shipped`/`shipped` casing
split — but it **never produces a report**. The session ends `status: failed` with
`report_md: "The agent did not produce a report within its turn limit."` This reproduced at
both the default 12-turn budget (43 transcript steps) and a raised 24-turn budget (79 steps).

See [Findings](#findings--recommendations) for the root cause and fix.

---

## Findings & recommendations

### F1 — RCA agent never calls `submit_report`, then discards its work (medium)

**Files:** `backend/app/llm/client.py` (`run_agent_loop`), `backend/app/llm/rca_agent.py`.

**Root cause.** In `run_agent_loop`, the `submit_report` final tool *is* offered to the model
every turn, but the nudge that reminds the model to call it only fires when the model returns
a turn with **no tool calls** (`if not response.tool_calls:`). A thorough model
(`claude-haiku-4.5` here) calls an investigation tool on *every* turn, so it is never nudged
and never submits; the loop simply exhausts `max_turns` and returns `None`. Raising
`DQ_LLM_MAX_RCA_TURNS` does not help (verified at 24) — more turns just buy more investigation.
On expiry the transcript is preserved in the DB, but `root_cause_summary`/`report_md` are
empty and the session is marked `failed`, so all the (correct) analysis is hidden from the user.

**Recommendations.**
1. **Turn-budget-aware nudge:** within the last 1–2 turns before `max_turns`, inject a message
   instructing the model to call `submit_report` now (optionally restricting `tools` to just the
   final tool on the last turn so it must submit).
2. **Graceful degradation on expiry:** instead of returning `None`, force one final
   submit-only turn, or synthesize a fallback report from the transcript, so the investigation
   isn't lost and the session can complete rather than fail.
3. Consider validating the agent loop against a non-fake provider in CI, since the current
   `FakeProvider` tests always submit and so don't exercise this path.

*Note:* this was observed with OpenRouter + Haiku. A model that more readily stops to submit
(or the Anthropic-native default) may mask it, but the loop logic gap is real regardless.

### F2 — Frontend bundle size (low / perf)
The main chunk is ~1.38 MB (403 KB gzip) and `WorkbenchPage` ~824 KB; Vite warns to code-split.
Routes are already lazy-loaded — the heavy vendor chunk (CodeMirror, recharts, xyflow) is the
target for `manualChunks`.

### F3 — Minor mobile clipping (low / polish)
At 375 px a child element inside `<main>` extends past the viewport's right edge (clipped, no
page-level horizontal scroll) — likely a chart/SVG. Cosmetic.

### Investigated and cleared (not bugs)
- **Workbench "Run" disabled with a query visible** — the editor is genuinely empty; the
  visible SQL is a CodeMirror *placeholder*. Disabling Run on an empty editor is correct.
- **Lineage graph drew 0 edges** — a headless-preview rendering artifact (React Flow's
  ResizeObserver doesn't fire in the automated preview). Edge data, dagre layout, and the
  legend's "6 edges" are all correct; it renders in a real browser.

---

## Appendix: ground-truth verification

LLM claims were re-checked with independent read-only SQL via the guarded query API. All matched:

| Claim (LLM feature) | Independent result |
|---|---|
| 590 total/line-item mismatches (RCA) | **590** ✓ |
| 131 sign-errors of 222 negative orders (RCA) | **131 / 222** ✓ |
| 592 orphan `order_id`s (RCA) | **592** ✓ |
| Total revenue ≈ $15.6M; min −$28,330; max $56,831.59 (dashboard) | **$15,596,567.92 / −28,330.0 / 56,831.59** ✓ |
| Lowercase `us` country present (check-gen) | **2 rows** ✓ |
| `loyalty_tier` domain = bronze/gold/platinum/silver (check-gen) | exact match ✓ |
| Malformed emails `not-an-email` / `a@b` present (check-gen) | **29 rows** ✓ |
| 4,835 distinct emails, 112 nulls of 5,000 (check-gen) | exact match ✓ |
| `orders` has 97 open exceptions (chat) | **97** ✓ |

No hallucinations were found in the four working LLM features.
