# Security Policy

## Supported versions

DQ Sentinel is pre-1.0. Security fixes land on `main` (and in the most recent
release, once releases are tagged). Older commits are not patched — please
upgrade to the latest `main` before reporting.

## Reporting a vulnerability

**Please do not open a public issue for security vulnerabilities.**

Preferred: use GitHub's private vulnerability reporting — on the repository page,
go to **Security → Report a vulnerability**. This keeps the report private while
it is triaged and fixed.

If that is not possible, email **metbron@gmail.com** with a description, steps to
reproduce, and the impact you believe it has.

This is a solo-maintained project: expect an acknowledgement within a few days
and a coordinated fix/disclosure timeline agreed with you. Credit is given in
the release notes unless you prefer otherwise.

## Scope notes for researchers

Things that are *by design* and useful context when assessing impact:

- **Source databases are read-only.** Every query against a registered source —
  including LLM-agent-authored SQL — must pass
  `backend/app/connectors/safety.py: guard_sql()` (single SELECT/CTE statement,
  denylist, forced row limit), and connectors open read-only where the driver
  supports it. A confirmed write path to a source database is a critical
  vulnerability; please report it privately.
- **Secrets come from environment variables** (see `.env.example`). With
  `DQ_ENV=prod`, the app refuses to boot on known-insecure defaults.
- **PII columns** flagged in a dataset's knowledge are redacted from LLM prompts
  and tool results.
- The production-hardening backlog is tracked publicly in
  [epic #160](https://github.com/k-sandhu/dq-sentinel/issues/160) — known,
  already-tracked hardening gaps listed there don't need a private report, but
  anything exploitable beyond what's described is very welcome.
