# dqsentinel (backend)

The FastAPI backend of [DQ Sentinel](https://github.com/k-sandhu/dq-sentinel) —
LLM-native data-quality monitoring: connect a database read-only, profile it,
generate and schedule checks, triage exceptions, and hand failures to an
agentic root-cause analyst.

This package is currently distributed **from source** as part of the monorepo:

```bash
pip install -e "backend[dev]"          # from the repo root
pip install -e "backend[postgres]"     # optional engine extras: postgres, mysql,
                                       # mssql, snowflake, bigquery, trino,
                                       # clickhouse, all-connectors
```

See the [repository README](https://github.com/k-sandhu/dq-sentinel#readme) for
the full quickstart (frontend, worker, sample data) and
[CONTRIBUTING](https://github.com/k-sandhu/dq-sentinel/blob/main/CONTRIBUTING.md)
for development docs.
