# Product vision prototypes

Self-contained clickable HTML mockups — no build step, no dependencies. Open directly in a browser, or serve the folder (`python -m http.server 4690 --directory local-prototypes`; a `prototype` config also exists in `.claude/launch.json`).

| File | What it is |
|---|---|
| `dq-sentinel-clickable-prototype.html` | Vision for **DQ Sentinel** itself: control center, incidents, datasets, AI-generated checks, exception triage, RCA agent, guarded workbench, governance, executive digest. |
| `sentinel-im-clickable-prototype.html` | The same platform vision applied to an **institutional investment manager** ("Northlake IM"): 10 clients across 5 custodians, vendor feeds (Bloomberg, Refinitiv, ICE, FactSet, Aladdin SoR), mandates/IPS guardrails, pools, IBOR-vs-custodian recon, trades & settlement, allocations vs policy, rebalancing with data-quality gates, performance shadow calc, ABOR/IBOR accounting tie-out, and a client reporting calendar. |

Both share the same conventions: a single file, hash-routed SPA in vanilla JS (central `data`/`state` objects, one delegated click handler, full re-render). Both include a **Sentinel Copilot** panel with scripted evidence-backed answers, a multi-stop **product tour**, a timed **live demo** (DQ: a sev1 unfolding; IM: the 10:30 Bloomberg refetch recovery), **dark mode** (persisted per file in localStorage), and a Ctrl+K command palette.

These are narrative artifacts, not production code — all data is mocked in-page and mutations reset on reload.

Note: anything else dropped into this folder stays local (`local-prototypes/` is listed in `.git/info/exclude`); these files are tracked because they were force-added.
