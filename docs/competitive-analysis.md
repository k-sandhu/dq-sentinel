# DQ Sentinel — Competitive Analysis & Feature Comparison Matrix

> **What this is.** A thorough, evidence-based comparison of **DQ Sentinel** against the
> open-source and commercial data-quality / data-observability landscape, mapping **what we
> have** and **what we don't**. The DQ Sentinel column is grounded in a full read of the
> codebase (`backend/app/core`, `connectors`, `llm`, `api`, `models.py`, `observability.py`,
> `frontend/src`). Competitor columns reflect public docs/positioning as of **mid-2026**
> (sources at the bottom); commercial tiers move fast, so treat 🟡 cells as "varies by tier."
>
> **Legend:** ✅ native / strong · 🟡 partial / limited / add-on / paid-tier / beta · ❌ none / not applicable · — out of scope for that tool

---

## 1. The landscape (who we're comparing against)

| Bucket | Tools | Shape |
|---|---|---|
| **OSS test libraries** | **Great Expectations** (GX Core/Cloud), **Soda Core** (+SodaCL), **dbt tests** (+dbt-expectations/-utils, unit tests, contracts), **deequ/PyDeequ**, **Pandera** | Code/YAML-defined assertions run in your pipeline/warehouse |
| **OSS observability (dbt-native)** | **Elementary**, re_data | Anomaly detection + test dashboards layered on dbt artifacts |
| **Diff / CI** | **Datafold** (data-diff OSS + Cloud) | Value-level diffing + column-level lineage + PR gating |
| **Commercial observability** | **Monte Carlo**, **Bigeye**, **Metaplane** (Datadog), Sifflet, Acceldata | SaaS, ML auto-monitoring + lineage + incidents |
| **AI-native DQ** | **Anomalo**, **Soda Cloud / Soda AI** | Unsupervised ML, "monitor everything," generative RCA |
| **Enterprise governance** | **Collibra DQ** (ex-OwlDQ), **Informatica** (CLAIRE), **Ataccama ONE** | DQ fused with catalog/MDM/lineage/policy/stewardship |
| **Warehouse-native** | **Snowflake** Data Metric Functions, **Databricks** Lakehouse Monitoring / DLT expectations | DQ primitives built into the platform |

Column headers used below: **DQ-S** = DQ Sentinel · **GX** = Great Expectations · **Soda** = Soda Core+Cloud · **dbt+El** = dbt tests + Elementary · **MC** = Monte Carlo · **Anom** = Anomalo · **Bigeye** · **Ent** = Collibra/Informatica/Ataccama · **Native** = Snowflake DMF / Databricks.

---

## 2. Platform, deployment & access

| Capability | DQ-S | GX | Soda | dbt+El | MC | Anom | Bigeye | Ent | Native |
|---|---|---|---|---|---|---|---|---|---|
| Self-hosted / open-source core | ✅ | ✅ | ✅ | ✅ | ❌ | 🟡¹ | ❌ | 🟡 | — |
| Data stays in source (no egress; read-only) | ✅ | ✅ | ✅ | ✅ | 🟡 | ✅¹ | 🟡 | 🟡 | ✅ |
| Managed SaaS option | ❌ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Built-in web UI (no BYO BI) | ✅ | 🟡² | ✅ | 🟡 | ✅ | ✅ | ✅ | ✅ | ❌ |
| Built-in scheduler/worker (no external orchestrator) | ✅ | ❌ | ✅ | ❌³ | ✅ | ✅ | ✅ | ✅ | ✅ |
| RBAC roles | ✅⁴ | 🟡 | ✅ | 🟡 | ✅ | ✅ | ✅ | ✅ | ✅ |
| SSO / SAML / OIDC | ❌ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Per-dataset / per-connection RBAC | ❌ | 🟡 | ✅ | 🟡 | ✅ | ✅ | ✅ | ✅ | ✅ |
| Multi-tenancy / workspaces | ❌ | 🟡 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Audit log | ✅ | 🟡 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Secrets at rest (DSN encryption / vault) | ❌⁵ | 🟡 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Self-observability (Prometheus/Grafana/Loki) | ✅⁶ | ❌ | — | ❌ | — | — | — | — | — |
| HA / Kubernetes / Helm | ❌ | 🟡 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

¹ Anomalo offers in-VPC / Snowflake Native App deployment, so data can stay in your account. · ² GX ships static "Data Docs" HTML; the interactive UI is GX Cloud. · ³ dbt needs Airflow/Dagster/dbt Cloud to schedule. · ⁴ Three global roles (viewer/editor/admin). · ⁵ DSN stored plaintext today (encryption is on the backlog). · ⁶ Unusual for the category — first-class metrics/logs stack ships in compose.

---

## 3. Checks & detection

| Capability | DQ-S | GX | Soda | dbt+El | MC | Anom | Bigeye | Ent | Native |
|---|---|---|---|---|---|---|---|---|---|
| Data profiling | ✅ | ✅ | ✅ | 🟡 | ✅ | ✅ | ✅ | ✅ | 🟡 |
| Declarative rule checks (null/unique/accepted/range/length/regex) | ✅⁷ | ✅⁸ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Custom SQL checks (read-only guarded) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Freshness monitoring | ✅ | 🟡 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Volume / row-count anomaly | ✅⁹ | 🟡 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | 🟡 |
| Schema-change detection (first-class) | ❌ | 🟡 | ✅ | ✅¹⁰ | ✅ | ✅ | ✅ | ✅ | 🟡 |
| Auto-suggested checks (profile-based) | ✅¹¹ | 🟡 | ✅ | ❌ | ✅ | ✅ | ✅ | ✅ | ❌ |
| Unsupervised "monitor everything, no rules" ML | 🟡¹² | ❌ | ✅ | 🟡 | ✅ | ✅ | ✅ | ✅ | ❌ |
| Statistical distribution drift (PSI / KS) | ✅¹³ | 🟡 | 🟡 | 🟡 | ✅ | ✅ | ✅ | ✅ | ❌ |
| Multivariate outlier detection (IsolationForest) | ✅¹⁴ | ❌ | 🟡 | ❌ | 🟡 | ✅ | 🟡 | 🟡 | ❌ |
| Time-series / seasonality-aware forecasting anomaly | ❌¹⁵ | ❌ | ✅ | 🟡 | ✅ | ✅ | ✅ | ✅ | ❌ |
| Referential-integrity / cross-table checks | 🟡¹⁶ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | 🟡 |
| Unstructured / text / GenAI data quality | ❌ | ❌ | 🟡 | ❌ | ✅ | ✅ | 🟡 | 🟡 | ❌ |

⁷ 12 check types (see §6). · ⁸ GX ships hundreds of "Expectations." · ⁹ `row_count_min` + `row_count_anomaly` (z-score vs history). · ¹⁰ dbt **model contracts** enforce schema. · ¹¹ Heuristic generator from profile stats. · ¹² ML outlier + drift exist as opt-in check *types*, but DQ-S still expects checks to be defined (auto-gen helps); it is not the zero-config "watch every column automatically" paradigm of Anomalo / Soda RAD. · ¹³ `distribution_drift` (PSI vs profile baseline, KS run-over-run) — a packaged first-class check, which is uncommon in OSS. · ¹⁴ First-class `ml_outlier` (sklearn IsolationForest, deterministic). · ¹⁵ Anomaly is z-score/PSI/KS, not Prophet-style seasonal forecasting. · ¹⁶ Must be expressed via `custom_sql`; no dedicated foreign-key check.

---

## 4. AI / LLM capabilities — **DQ Sentinel's strongest differentiation**

| Capability | DQ-S | GX | Soda | dbt+El | MC | Anom | Bigeye | Ent | Native |
|---|---|---|---|---|---|---|---|---|---|
| LLM-proposed checks | ✅ | ❌ | ✅¹⁷ | ❌ | 🟡 | ✅ | 🟡 | 🟡 | ❌ |
| AI exploration agent (writes read-only SQL to learn *before* proposing) | ✅¹⁸ | ❌ | ❌ | ❌ | 🟡 | 🟡 | ❌ | ❌ | ❌ |
| Agentic root-cause analysis (tool-use loop, evidence-backed report) | ✅¹⁹ | ❌ | 🟡 | ❌ | ✅²⁰ | ✅ | 🟡 | 🟡 | ❌ |
| Conversational assistant over your data + platform (chat) | ✅²¹ | ❌ | 🟡 | ❌ | 🟡 | 🟡 | ❌ | 🟡 | ❌ |
| **Bring-your-own / local & open models (provider-agnostic)** | ✅²² | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| **MCP integration (pull in dbt/repo/doc context)** | ✅²³ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Inline AI-generated dashboards / panels | ✅ | ❌ | 🟡 | ❌ | 🟡 | 🟡 | ❌ | 🟡 | ❌ |
| PII redaction before prompts | ✅²⁴ | — | 🟡 | — | ✅ | ✅ | 🟡 | ✅ | — |
| Works fully **without** any AI (graceful degradation) | ✅ | ✅ | ✅ | ✅ | — | ❌²⁵ | — | — | ✅ |
| Auto-triggered RCA on check failure | ❌²⁶ | ❌ | 🟡 | ❌ | ✅ | ✅ | 🟡 | 🟡 | ❌ |

¹⁷ Soda AI 4.0 (Cleanse / Contract Autopilot). · ¹⁸ Bounded explorer loop (default 8 turns) — genuinely uncommon. · ¹⁹ RCA agent: read-only SQL + `get_table_code`, returns markdown report + confidence + suggested fixes + full transcript. · ²⁰ Monte Carlo's "Troubleshooting Agent" reached GA in the 2025–26 cycle; Anomalo has automated RCA — these are the closest peers, but both are paid SaaS. · ²¹ WebSocket chat with `run_sql`, `get_table_code`, `render_chart` (inline charts). · ²² Anthropic **or any** OpenAI-compatible endpoint (OpenRouter / vLLM / Ollama / Together) — you can run a **fully local** model. No commercial DQ tool offers this. · ²³ Anthropic MCP connector consumes external MCP servers for code context. (DQ-S does *not yet expose its own* MCP server — see gaps.) · ²⁴ `pii_columns` from the knowledge base are redacted; ≤25 sample rows; all agent SQL passes `guard_sql()`. · ²⁵ AI is the product for Anomalo. · ²⁶ RCA is on-demand only today.

**Bottom line on AI:** On the agentic/LLM dimension DQ Sentinel is **ahead of the entire OSS field** and **competitive with commercial leaders** (Monte Carlo, Anomalo) on RCA + check-gen — while being the **only** option that is simultaneously self-hostable, provider-agnostic (incl. local models), and MCP-aware. That combination is effectively unique as of mid-2026.

---

## 5. Lineage, metadata, workflow, alerting & ops

| Capability | DQ-S | GX | Soda | dbt+El | MC | Anom | Bigeye | Ent | Native |
|---|---|---|---|---|---|---|---|---|---|
| Lineage — table level | ✅²⁷ | ❌ | 🟡 | ✅ | ✅ | 🟡 | ✅ | ✅ | 🟡 |
| Lineage — **column level** | ❌ | ❌ | 🟡 | 🟡 | ✅ | 🟡 | ✅ | ✅ | 🟡 |
| Lineage from query logs (not just DDL) | ❌²⁸ | ❌ | 🟡 | ❌ | ✅ | 🟡 | ✅ | ✅ | ✅ |
| Check-health overlay on lineage | ✅²⁹ | ❌ | 🟡 | 🟡 | ✅ | 🟡 | ✅ | 🟡 | ❌ |
| Business knowledge base / glossary / ownership | ✅³⁰ | ❌ | 🟡 | 🟡 | 🟡 | 🟡 | 🟡 | ✅ | 🟡 |
| **Data contracts** | ❌ | 🟡 | ✅ | ✅ | 🟡 | 🟡 | 🟡 | ✅ | 🟡 |
| Incident/exception triage workflow | ✅³¹ | ❌ | ✅ | 🟡 | ✅ | ✅ | ✅ | ✅ | ❌ |
| "Expected/known-issue" feedback loop | ✅³² | ❌ | 🟡 | ❌ | ✅ | ✅ | 🟡 | 🟡 | ❌ |
| Recurrence / identity tracking (dedup, auto-resolve, regression) | ✅³³ | ❌ | 🟡 | ❌ | ✅ | ✅ | ✅ | 🟡 | ❌ |
| SLA tracking / enforcement | 🟡³⁴ | ❌ | ✅ | 🟡 | ✅ | 🟡 | ✅ | ✅ | ❌ |
| Alerting — Slack / Email | ✅ | 🟡 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | 🟡 |
| Alerting — PagerDuty/Opsgenie/Teams/webhook | ❌³⁵ | 🟡 | ✅ | 🟡 | ✅ | ✅ | ✅ | ✅ | ❌ |
| Alert digests / escalation / on-call routing | ❌ | ❌ | ✅ | 🟡 | ✅ | 🟡 | ✅ | ✅ | ❌ |
| CI/CD PR gating / data diff | ❌³⁶ | ✅ | ✅ | ✅ | 🟡 | 🟡 | 🟡 | 🟡 | 🟡 |
| Ad-hoc SQL workbench + saved queries | ✅³⁷ | ❌ | ❌ | ❌ | 🟡 | 🟡 | 🟡 | 🟡 | — |
| Custom dashboards / reporting UI | ✅³⁸ | 🟡 | ✅ | 🟡 | ✅ | ✅ | ✅ | ✅ | ❌ |
| Read-only SQL guard on **all** queries (incl. AI) | ✅³⁹ | — | 🟡 | — | 🟡 | 🟡 | 🟡 | 🟡 | ✅ |
| Source-engine breadth | 🟡⁴⁰ | ✅ | ✅ | 🟡 | ✅ | 🟡 | ✅ | ✅ | ❌ |

²⁷ sqlglot parses view DDL → table-level graph. · ²⁸ DDL/view-definition only; no CTAS/query-history lineage; no cross-connection federation. · ²⁹ pass/warn/fail/unknown per node from live check + exception state — a nice touch. · ³⁰ `TableKnowledge`: business context, owner, importance, freshness SLA, PII columns, known issues — and it **feeds the AI agents**. · ³¹ open → acknowledged / expected / resolved / muted, with assignment, comments, append-only event trail, faceted search, bulk ops, CSV export. Commercial-grade. · ³² "expected" markings flow back into the knowledge base. · ³³ SHA-256 fingerprint identity → occurrence counts, auto-resolve on passing runs, auto-reopen on regression. · ³⁴ Freshness SLA is stored in the knowledge base but not enforced/tracked as an SLA with MTTR dashboards. · ³⁵ Only Slack + Email channels today. · ³⁶ No PR-gate / environment diffing (Datafold's home turf). · ³⁷ Guarded SELECT workbench + shareable saved-query library — uncommon in pure DQ tools. · ³⁸ System dashboard + LLM/heuristic ad-hoc dashboards + a custom widget builder (metric/exceptions/checks/SQL/note). · ³⁹ `guard_sql()` (single SELECT/CTE, keyword denylist, forced LIMIT, literal/comment stripping) on custom checks, workbench, saved queries, dashboard SQL **and every LLM-authored query** — a strong, consistent safety posture. · ⁴⁰ 9 SQL engines (SQLite, DuckDB, PostgreSQL, MySQL, SQL Server, Snowflake, BigQuery, Trino, ClickHouse); no NoSQL, SaaS-app, streaming/CDC, file/object-store, or BI-tool sources.

---

## 6. DQ Sentinel's own check inventory (for reference)

12 first-class check types, all read-only and dialect-aware:

| # | Type | Level | What it does |
|---|---|---|---|
| 1 | `not_null` | column | No NULLs (supports `tolerance`) |
| 2 | `unique` | column | All values distinct; reports duplicate groups |
| 3 | `accepted_values` | column | Enum membership (case-toggle) |
| 4 | `range` | column | Numeric/date bounds (open-ended ok) |
| 5 | `string_length` | column | Length bounds |
| 6 | `regex_match` | column | Pattern match (native regex; Python fallback on SQLite) |
| 7 | `freshness` | column | Newest timestamp within SLA (excludes future-dated rows) |
| 8 | `row_count_min` | table | Minimum row threshold |
| 9 | `row_count_anomaly` | table | Z-score vs rolling history (σ, lookback, min-history) |
| 10 | `custom_sql` | table | User SELECT returning violating rows (guarded) |
| 11 | `ml_outlier` | table | IsolationForest multivariate outliers (deterministic, top-N) |
| 12 | `distribution_drift` | column | PSI vs profile baseline **or** KS run-over-run |

Supporting machinery: a profiler (SQL aggregates + pandas sample stats: quantiles, patterns, PK candidates, top-values), a heuristic + LLM check generator, a cron/interval **scheduler** with optimistic-CAS claiming, severity → status mapping with tolerance, and the exception reconciliation/triage lifecycle described above.

---

## 7. What we **have** (distinctive or commercial-grade strengths)

1. **Agentic, self-hostable AI** — LLM check-gen, a pre-proposal **exploration agent**, an **RCA agent** (read-only SQL tool-loop with evidence-backed reports), and a **conversational assistant** with inline charts. The OSS field has nothing comparable; this rivals Monte Carlo/Anomalo's newest paid AI.
2. **Provider-agnostic AI incl. local models** — Anthropic *or any* OpenAI-compatible endpoint (OpenRouter/vLLM/Ollama). A real privacy/sovereignty story no commercial competitor matches.
3. **MCP-aware** — agents can pull in dbt/repo/doc context via MCP. Essentially unique in the DQ space.
4. **ML as first-class checks** — IsolationForest multivariate outliers + PSI/KS drift + z-score volume anomaly, packaged and deterministic.
5. **Commercial-grade exception triage** — fingerprint identity, recurrence counts, auto-resolve, regression re-open, assignment, comments, append-only audit, faceted search, bulk ops, CSV export.
6. **Knowledge base that feeds the AI** — business context, owner, SLA, importance, PII columns; "expected" markings loop back in.
7. **Lineage with a live check-health overlay.**
8. **Strong, uniform safety** — `guard_sql()` on *every* query path including AI; PII redaction in prompts; read-only connections per driver; data never leaves the source.
9. **Batteries-included ops** — built-in scheduler/worker (no Airflow needed), Prometheus + Grafana + Loki self-observability, structured logs with request-ID correlation, audit log, RBAC.
10. **Analyst UX** — ad-hoc SQL workbench, saved-query library, system + ad-hoc + custom dashboards, global command-K search.
11. **Fully OSS / self-hosted** — no per-seat SaaS bill, no vendor lock-in, no data egress.

## 8. What we **don't** have (gaps), roughly by impact

**Enterprise blockers**
- ❌ **SSO/SAML/OIDC**, MFA, session revocation (local JWT only)
- ❌ **Multi-tenancy** and per-connection / per-dataset RBAC
- ❌ **DSN encryption at rest** / secrets vault (stored plaintext)
- ❌ HA / Kubernetes / Helm / horizontal worker scaling; no managed cloud

**Observability/monitoring depth**
- ❌ **Column-level lineage**; query-log/CTAS lineage; cross-connection federation
- ❌ **Schema-change detection** as a first-class monitor
- ❌ **SLA tracking/enforcement** with MTTR/incident dashboards
- 🟡 **Unsupervised "watch everything, zero rules"** monitoring (Anomalo / Soda RAD) — we still need checks defined
- ❌ Seasonality-aware / forecasting anomaly (Prophet-style)

**Integrations & workflow**
- ❌ Alerting beyond **Slack/Email** (no PagerDuty/Opsgenie/Teams/generic webhook), no digests/escalation/on-call
- ❌ **Data contracts** / schema-evolution enforcement
- ❌ **CI/CD PR gating & data diff** (Datafold-style)
- ❌ Auto-triggered RCA on failure (on-demand only); DQ-S does not yet **expose its own** MCP server

**Coverage & data**
- 🟡 Connectivity is **SQL-only** (9 engines) — no NoSQL, SaaS apps, streaming/CDC, files/object storage, BI tools
- ❌ Automated PII/sensitive-column **classification** (manual tagging today)
- ❌ Unstructured / text / GenAI data-quality monitoring (Anomalo)
- 🟡 Native referential-integrity check (use `custom_sql`); no historical-profile comparison UI

---

## 9. Positioning & best fit

DQ Sentinel sits at the **intersection of three categories**:

- vs **OSS test libraries (GX / Soda Core / dbt)** — it *adds* the full product layer they lack: a UI, a scheduler, triage workflow, lineage, dashboards, and AI.
- vs **commercial observability (Monte Carlo / Bigeye)** — it matches incident triage, anomaly/drift, and lineage-health, but trails on column-level/query-log lineage, SLAs, integrations, and zero-config auto-monitoring.
- vs **AI-native DQ (Anomalo / Soda AI)** — it is competitive (and on agentic RCA + bring-your-own-LLM + MCP, arguably ahead), while lacking their at-scale unsupervised "no rules" coverage and unstructured-data support.

**Closest one-line analogue:** *a self-hostable, AI-agent-forward blend of Soda Cloud + a lightweight Monte Carlo, wearing a Metabase-style triage UI.*

**Best fit:** mid-scale data teams (~10–200 datasets) on SQL warehouses who want open-source control, **data residency / no egress**, batteries-included ops, and modern **LLM-assisted diagnostics** without Monte-Carlo/Anomalo pricing — especially teams that need to run a **local model** for compliance.

**Weakest fit:** large regulated enterprises needing SSO + multi-tenancy + column-level lineage + data contracts + PagerDuty/on-call + "monitor everything automatically," or shops whose data isn't primarily in SQL warehouses.

---

## 10. Recommendations (highest-leverage gaps to close)

**To become enterprise-credible (table-stakes):**
1. SSO/OIDC + DSN encryption at rest (unblocks procurement).
2. Alerting fan-out: PagerDuty/Opsgenie/Teams/generic webhook + digests/escalation.
3. First-class **schema-change** monitor and **SLA tracking** dashboards.
4. **Column-level lineage** (sqlglot already parses statements — extend to projections) and query-log lineage.
5. **Data contracts** (schema + freshness + volume) with CI enforcement.

**To extend the AI lead (where we already win):**
6. Auto-trigger RCA on failure and attach the report to the incident.
7. NL→check authoring inside the chat assistant; expose DQ-S as its **own MCP server**.
8. Seasonality-aware/forecasting anomaly; an Anomalo-style "auto-monitor every column" mode built on the existing profiler + drift engine.
9. Unstructured/text DQ using the provider-agnostic LLM layer already in place.

---

## Sources

Competitor capabilities cross-checked against public materials (mid-2026):

- Monte Carlo — [AI/agents & RCA](https://www.montecarlodata.com/platform/root-cause-analysis), [agent launch](https://www.techtarget.com/searchdatamanagement/news/366622933/Monte-Carlo-launches-first-agents-for-data-observability), [AI features docs](https://docs.getmontecarlo.com/docs/ai-features-and-technical-info)
- Anomalo — [product overview](https://www.anomalo.com/product-overview/), [unstructured/GPT-4 text monitoring](https://www.streetinsider.com/Globe+Newswire/Anomalo+Adds+AI-Powered+Monitoring+of+Unstructured+Text+to+Its+Data+Quality+Platform/23351977.html), [Snowflake Native App](https://www.globenewswire.com/news-release/2024/09/25/2953203/0/en/Anomalo-Launches-First-Fully-Containerized-Native-Data-Quality-App-for-Observability-on-Snowflake-Marketplace.html)
- Great Expectations / Soda — [GX Core](https://greatexpectations.io/gx-core/), [Soda vs GX](https://www.dataexpert.io/blog/soda-vs-great-expectations-data-quality-tools), [Soda 4.0 / Soda AI](https://soda.io/blog/introducing-soda-4.0), [Soda AI](https://soda.io/product/soda-ai), [automated monitoring](https://docs.soda.io/soda-cl-overview/automated-monitoring)
- Bigeye — [platform](https://www.bigeye.com/platform/data-observability), [review](https://www.siffletdata.com/blog/bigeye-review)
- dbt + Elementary — [dbt data quality guide](https://atlan.com/dbt-data-quality/), [Elementary tests](https://docs.elementary-data.com/data-tests/introduction), [dbt-data-reliability](https://github.com/elementary-data/dbt-data-reliability)
- Datafold / deequ — [data-diff vs Cloud](https://www.datafold.com/blog/the-lowdown-open-source-data-diff-vs-datafold-cloud/), [Datafold lineage](https://docs.datafold.com/data-explorer/lineage), [PyDeequ](https://github.com/awslabs/python-deequ)
- Enterprise governance — [Collibra](https://www.collibra.com/), [Ataccama/Informatica overview](https://www.alation.com/blog/data-management-software/)
- Warehouse-native — [Snowflake DMFs](https://docs.snowflake.com/en/sql-reference/functions-data-metric), [Databricks/Snowflake DQ guide](https://atlan.com/know/data-observability-best-practices-snowflake/)

> *DQ Sentinel column derived from source: `core/check_types.py`, `core/profiler.py`, `core/runner.py`, `core/scheduler.py`, `core/ml.py`, `core/lineage.py`, `connectors/{dialects,safety,sa}.py`, `llm/*`, `api/*`, `models.py`, `observability.py`, `frontend/src/`.*
