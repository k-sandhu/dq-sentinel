#!/usr/bin/env node
/**
 * npm-audit gate with expiring, per-advisory exceptions.
 *
 * Usage:  npm audit --omit=dev --json | node scripts/audit-allowlist.mjs
 *
 * WHY THIS EXISTS
 * npm has no native ignore mechanism (npm 10.x `npm audit` accepts only
 * `--audit-level`), so the two obvious options are both bad:
 *   - leave the job permanently red, which trains everyone to ignore the one
 *     check that will report the NEXT real CVE; or
 *   - blunt it with `--audit-level=critical`, which silently swallows every
 *     future high advisory too.
 * This filter is the third option: fail on anything that is not explicitly,
 * and unexpiredly, justified.
 *
 * PROPERTIES THAT KEEP IT HONEST
 *   - An exception carries an expiry. Past it, CI fails until someone
 *     re-justifies or fixes it. A justification cannot outlive its reasoning.
 *   - A STALE exception (allowlisted advisory no longer reported) also fails,
 *     so the list cannot quietly accumulate dead entries.
 *   - Severity is ignored on purpose: a `moderate` that is not allowlisted
 *     fails just like a `critical`. `--audit-level` is deliberately unused.
 *
 */
import { readFileSync } from "node:fs";

/** @type {{id: string, package: string, expires: string, why: string}[]} */
const ALLOW = [];

const raw = readFileSync(0, "utf8").trim();
if (!raw) {
  console.error("npm audit gate failed: no JSON on stdin (did `npm audit --json` run?).");
  process.exit(1);
}

let report;
try {
  report = JSON.parse(raw);
} catch (err) {
  console.error(`npm audit gate failed: could not parse audit JSON — ${err.message}`);
  process.exit(1);
}

// npm reports registry/network trouble as a JSON body too. Treat that as a
// failure rather than "no vulnerabilities found", which would otherwise let a
// broken audit pass silently AND mark every exception stale.
if (report.error) {
  const { code, summary, detail } = report.error;
  console.error(`npm audit gate failed: audit did not run — ${code ?? ""} ${summary ?? ""} ${detail ?? ""}`.trim());
  process.exit(1);
}
if (!report.vulnerabilities || typeof report.vulnerabilities !== "object") {
  console.error("npm audit gate failed: audit JSON has no `vulnerabilities` map (unexpected npm output shape).");
  process.exit(1);
}

// Collect (package, advisory) pairs. `via` holds either a string (a pointer to
// the dependency that drags the vuln in — counted at its source, so skipped) or
// the advisory object itself. The same advisory can surface under several
// packages, so dedupe on package+id.
const seen = new Map();
for (const [pkg, node] of Object.entries(report.vulnerabilities)) {
  for (const via of node.via ?? []) {
    if (typeof via === "string") continue;
    const id = String(via.url ?? "").split("/").pop();
    if (!id) continue;
    const key = `${pkg}::${id}`;
    if (!seen.has(key)) {
      seen.set(key, { pkg, id, severity: via.severity ?? "unknown", title: via.title ?? "(untitled)" });
    }
  }
}
const found = [...seen.values()];

const today = new Date().toISOString().slice(0, 10);
const problems = [];
const matched = new Set();

for (const f of found) {
  const entry = ALLOW.find((a) => a.id === f.id && a.package === f.pkg);
  if (!entry) {
    problems.push(`NOT ALLOWLISTED   [${f.severity}] ${f.pkg}  ${f.id}  ${f.title}`);
    continue;
  }
  matched.add(entry.id);
  if (entry.expires <= today) {
    problems.push(
      `EXCEPTION EXPIRED (${entry.expires})  ${f.pkg}  ${f.id} — re-justify with a new expiry, or fix it`,
    );
  } else {
    console.log(`allowlisted until ${entry.expires}: ${f.pkg} ${f.id}\n    ${entry.why}`);
  }
}

for (const a of ALLOW) {
  if (!matched.has(a.id)) {
    problems.push(`STALE EXCEPTION   ${a.package} ${a.id} is no longer reported — delete this entry`);
  }
}

if (problems.length) {
  console.error(`\nnpm audit gate FAILED:\n${problems.map((p) => `  ${p}`).join("\n")}\n`);
  process.exit(1);
}
console.log(`\nnpm audit gate passed — ${found.length} advisory/ies, all explicitly justified and unexpired.`);
