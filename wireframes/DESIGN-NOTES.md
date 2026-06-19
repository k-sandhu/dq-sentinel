# DQ Sentinel wireframes — design critique & v2 direction

My opinion, after the competitive scan, on what we've already built, what to change, and —
just as important — **what to deliberately leave out** so the product stays sharp instead of
becoming another bloated enterprise console.

---

## What's good in v1 (keep)

- **The three-direction switcher.** Being able to flip Aurora/Graphite/Editorial live is the
  single best decision — it turns "pick a look" from an argument into an experiment. Keep it.
- **Token-driven design system.** One CSS file, everything re-skins. This is what lets v2 add ten
  screens without ten stylesheets. Keep and extend.
- **Triage-centric IA.** Leading with Overview → Exceptions → Dataset-detail matches how the market's
  best (Metaplane, Anomalo) actually work. Our exception drawer is on the right track.
- **The dataset-detail tab set** (Profile/Code/Lineage/Checks/Runs/Exceptions/RCA/Knowledge) is a
  genuinely strong "one object, all its facets" pattern. Don't fragment it.
- **AI shown as steps, not magic.** The assistant's visible tool-calls are exactly the 2025 pattern.
  Lean harder into this.

## What's weak in v1 (fix)

- **Overview is a flat KPI wall.** It doesn't follow the "inverted pyramid" (status → trend → worklist)
  the whole category converged on, and it's missing the metrics buyers now expect: **coverage %,
  MTTD/MTTR, SLA attainment, a dimension scorecard.** → restructure.
- **No incident concept.** We jump straight from "exception" (a row-level alert) to triage, with no way
  to group correlated failures into one owned, severity-rated thing. This is the biggest gap vs. every
  observability leader. → add Incidents as a tier above Exceptions.
- **Triage is too thin.** No severity, no assignee, and "expected" vs "false-positive" are collapsed —
  but those two feed the system differently (one is knowledge, one is detection tuning). And we show the
  *offending* rows but never *why* they're wrong. → add severity/assignee + the good-vs-bad-rows +
  column-attribution view (Anomalo's pattern).
- **Lineage is a static row of boxes.** Real lineage is an anchor-node neighborhood you expand by hops,
  with health propagation and an impact count. → upgrade.
- **Check creation is a dead "+ Add check" button.** Our actual superpower — the *exploration agent that
  writes SQL to learn the data before proposing checks* — is invisible. → make check-gen a reviewable
  recommendation flow that shows the agent's SQL trail.
- **RCA is a single block.** Should be tri-sectioned (what happened / analysis / next steps) with live
  streamed steps and a source/ETL/transform/model verdict.

## What should be there in v2 (add)

| Add | Why (from the scan) |
|---|---|
| **Incidents** surface + promote-from-exception | Alert→Incident two-tier is universal (Monte Carlo); grouping by lineage+time is the #1 noise lever |
| **Overview** restructured + scorecard + coverage/MTTR/SLA + incident heatmap | The category-standard health home (DQOps, Bigeye, Sifflet) |
| **Severity + assignee + why-it-failed** in triage | Metaplane/Anomalo triage; distinct Expected vs False-positive |
| **Lineage** neighborhood/hop/impact upgrade | Bigeye/Metaplane/Snowsight conventions |
| **Check-gen recommendation review** w/ exploration-agent SQL trace + **checks-as-code export** | GX ExpectAI / MC monitors-as-code — but the *exploration trace* is ours alone |
| **RCA** tri-section + streamed steps + hypothesis taxonomy | Monte Carlo Troubleshooting Agent; Azure observability-agent report shape |
| **Data Contracts** (editor + registry + breaking-change diff) | Soda 4.0 / ODCS — the 2025 framing; reuses our runner + lineage |
| **Alerting & routing** (audiences + rules, interactive Slack) | Table stakes for any team adoption |
| **Data status page** (read-only stakeholder timeline) | "Status page for data" (Datadog convention) |
| **CI / PR-comment** mock | Datafold's killer surface; positions checks as a merge-time gate |

## What should NOT be there (resist — this is where competitors got bloated)

- **No compute/cost/FinOps dashboards.** That's Acceldata's lane and it dilutes a quality tool. Skip.
- **No full data catalog / business glossary.** Sifflet and the governance incumbents (Collibra,
  Ataccama, Informatica) are heavy here; competing on catalog turns us into a 2-year enterprise build.
  Integrate with catalogs, don't become one.
- **No MDM / reference-data / stewardship-workflow sprawl.** Governance-suite territory; not our fight.
- **No auto-*fix*-the-source agent.** Every leader stops at recommend/investigate and stays **read-only**
  — and our golden rule (`guard_sql()`) forbids writes anyway. A "fix agent" should only ever *draft*
  changes (a check edit, a contract update) for human approval; it must never touch the source.
- **No "monitor literally everything" onboarding.** Anomalo/Lightup auto-cover every table, which buys
  noise and cost. Our profile → recommend → human-approves flow (Validio's model) is better; don't
  abandon it for vanity coverage numbers.
- **No agent-observability / LLM-tracing product.** Monte Carlo and Datadog are racing here; it's a
  different product. We *consume* LLMs; we shouldn't pivot to monitoring other people's models.
- **Keep the nav to ~3 groups.** The moment the sidebar passes ~12 items it reads as enterprise-bloat.
  Group as **Monitor / Build / Investigate** and push everything else into dataset-detail tabs.

## Navigation for v2 (IA)

```
Monitor      Overview · Incidents · Exceptions · My work
Build        Connections · Datasets · Checks · Contracts · Lineage
Investigate  Assistant · Workbench · Alerts & routing
             (Settings + Status page reachable, not top-level clutter)
```

Dataset-detail keeps its tab set; Incidents and Contracts get their own top-level homes because they
span datasets. Everything else stays a tab or a drawer, not a nav item.

## The one-sentence product position to design toward

> **Open, self-hostable data quality with agents that *investigate* your data — not just watch it.
> Read-only by design, your LLM or ours, across 9 engines.**

Every empty-state, hero, and onboarding step in v2 should ladder up to that sentence.
