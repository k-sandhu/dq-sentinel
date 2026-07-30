import { describe, expect, it } from "vitest";

import { resolvePostLoginTarget, safeInternalPath } from "./postLogin";

/**
 * Open-redirect tests are only meaningful if they assert on the RESOLVED
 * ORIGIN, not on the string. `"/\\evil.example"` looks like an internal path
 * and resolves to `https://evil.example` — a string assertion is exactly what
 * let the backslash bypass (CVE-2025-68470 class) ship in the first place.
 */
const APP_ORIGIN = "https://dq.internal";

function resolvedOrigin(candidate: string): string {
  return new URL(candidate, APP_ORIGIN).origin;
}

interface Attack {
  /** What the payload abuses. */
  name: string;
  input: string;
  /**
   * True when the RAW input escapes the app origin (verified below, so the
   * table can't rot into a list of harmless strings). The rest are payloads
   * that stay same-origin but are still not app locations: encoded separators
   * (one downstream decode away from being real ones) and relative paths.
   */
  crossOrigin?: true;
}

const ATTACKS: Attack[] = [
  // ── scheme-relative ────────────────────────────────────────────────────────
  { name: "scheme-relative", input: "//evil.example/phish", crossOrigin: true },
  { name: "triple slash", input: "///evil.example", crossOrigin: true },
  // ── backslash authority (the advisory's bypass class) ──────────────────────
  { name: "backslash authority", input: "/\\evil.example", crossOrigin: true },
  { name: "double backslash authority", input: "/\\\\evil.example", crossOrigin: true },
  { name: "backslash then slash", input: "\\/evil.example", crossOrigin: true },
  { name: "leading double backslash", input: "\\\\evil.example", crossOrigin: true },
  {
    name: "backslash with path, query and hash",
    input: "/\\evil.example/checks?x=1#row",
    crossOrigin: true,
  },
  { name: "backslash + userinfo", input: "/\\dq.internal@evil.example", crossOrigin: true },
  // ── characters the URL parser strips or trims before parsing ───────────────
  { name: "tab inside the authority", input: "/\t/evil.example", crossOrigin: true },
  { name: "tab then backslash", input: "/\t\\evil.example", crossOrigin: true },
  { name: "newline prefix", input: "\n//evil.example", crossOrigin: true },
  { name: "CRLF prefix", input: "\r\n//evil.example", crossOrigin: true },
  { name: "leading space", input: " //evil.example", crossOrigin: true },
  { name: "trailing space", input: "//evil.example ", crossOrigin: true },
  { name: "NUL byte", input: "/\u0000//evil.example" },
  { name: "non-breaking space prefix", input: "\u00a0//evil.example" },
  // ── absolute URLs / non-http schemes ───────────────────────────────────────
  { name: "absolute https", input: "https://evil.example/phish", crossOrigin: true },
  { name: "absolute with backslashes", input: "http:\\\\evil.example", crossOrigin: true },
  { name: "javascript:", input: "javascript:alert(document.domain)", crossOrigin: true },
  {
    name: "data:",
    input: "data:text/html,<script>alert(document.domain)</script>",
    crossOrigin: true,
  },
  { name: "vbscript:", input: "vbscript:msgbox(1)", crossOrigin: true },
  // ── percent-encoded separators (same-origin as written; rejected because a
  //    single downstream decode turns them into real separators) ─────────────
  { name: "encoded slashes", input: "/%2f%2fevil.example" },
  { name: "encoded slashes, upper case", input: "/%2F%2Fevil.example" },
  { name: "encoded backslashes", input: "/%5c%5cevil.example" },
  { name: "encoded, no leading slash", input: "%2f%2fevil.example" },
  { name: "encoded separator mid-path", input: "/datasets%2F..%2F%2Fevil.example" },
  // ── not an app location at all ─────────────────────────────────────────────
  { name: "relative path", input: "checks" },
  { name: "empty string", input: "" },
  { name: "bare login", input: "/login" },
  { name: "login with query", input: "/login?next=x" },
  { name: "login with hash", input: "/login#x" },
  { name: "login, upper case (routes match case-insensitively)", input: "/LOGIN" },
  { name: "login with trailing slash", input: "/login/" },
];

/** Deep links that must keep working — the guard is worthless if analysts lose
 *  their destination. Every real route shape in App.tsx is represented. */
const LEGITIMATE = [
  "/",
  "/my-work",
  "/checks",
  "/checks/42",
  "/datasets",
  "/datasets/3",
  "/datasets/3/checks",
  "/datasets/3/checks#top",
  "/exceptions?status=open&sel=12#row",
  "/exceptions?dataset_id=5&status=open",
  "/runs?check_id=9&day=2026-07-30",
  "/runs/118",
  "/connections/4/browse",
  "/dashboards/12?edit=1",
  "/workbench?dataset_id=5&saved_query_id=2",
  "/lineage?connection=2",
  "/docs/getting-started",
  "/settings",
  "/incidents",
  "/reliability",
  "/status",
  "/assistant",
  // Encoding inside the QUERY is legitimate and common (saved views, filters);
  // only the path portion rejects encoded separators.
  "/exceptions?q=order%20id&status=open",
  "/exceptions?next=%2Fchecks%3Fstatus%3Dactive",
  "/workbench?sql=select%20*%20from%20orders",
];

describe("safeInternalPath — attack table", () => {
  it("the cross-origin payloads really do escape the app origin (guards the guard)", () => {
    for (const { name, input, crossOrigin } of ATTACKS) {
      if (!crossOrigin) continue;
      expect(resolvedOrigin(input), `${name}: ${JSON.stringify(input)}`).not.toBe(APP_ORIGIN);
    }
  });

  it("rejects every payload, and the RESOLVED ORIGIN of the result stays same-origin", () => {
    for (const { name, input } of ATTACKS) {
      const result = safeInternalPath(input);
      const label = `${name}: ${JSON.stringify(input)}`;
      expect(result, label).toBe("/");
      expect(resolvedOrigin(result), label).toBe(APP_ORIGIN);
    }
  });

  it("falls back to '/' for non-strings and missing state", () => {
    for (const input of [undefined, null, 42, { pathname: "/checks" }, ["/checks"], true]) {
      const result = safeInternalPath(input);
      expect(result).toBe("/");
      expect(resolvedOrigin(result)).toBe(APP_ORIGIN);
    }
  });
});

describe("safeInternalPath — legitimate deep links", () => {
  it("passes real routes through unchanged, query and hash included", () => {
    for (const input of LEGITIMATE) {
      expect(safeInternalPath(input), input).toBe(input);
    }
  });

  it("every accepted target resolves to the app origin", () => {
    for (const input of LEGITIMATE) {
      expect(resolvedOrigin(safeInternalPath(input)), input).toBe(APP_ORIGIN);
    }
  });
});

describe("resolvePostLoginTarget", () => {
  it("prefers router state over the ?from= query", () => {
    expect(resolvePostLoginTarget("/checks", "?from=%2Fruns")).toBe("/checks");
  });

  it("falls back to ?from= (the API client's hard 401 redirect)", () => {
    expect(resolvePostLoginTarget(undefined, "?from=%2Fexceptions%3Fdataset_id%3D5")).toBe(
      "/exceptions?dataset_id=5",
    );
    expect(resolvePostLoginTarget(null, "?from=%2Fdatasets%2F3%2Fchecks")).toBe(
      "/datasets/3/checks",
    );
  });

  it("validates BOTH channels against the whole attack table", () => {
    for (const { name, input } of ATTACKS) {
      const viaState = resolvePostLoginTarget(input, "");
      const viaQuery = resolvePostLoginTarget(undefined, `?from=${encodeURIComponent(input)}`);
      const label = `${name}: ${JSON.stringify(input)}`;
      expect(viaState, `state ${label}`).toBe("/");
      expect(viaQuery, `query ${label}`).toBe("/");
      expect(resolvedOrigin(viaState), `state ${label}`).toBe(APP_ORIGIN);
      expect(resolvedOrigin(viaQuery), `query ${label}`).toBe(APP_ORIGIN);
    }
  });

  it("survives the query channel's own decode (double-encoded separators)", () => {
    // URLSearchParams decodes once: "%252F" -> "%2F", which the path rule then
    // rejects. Without that rule this would arrive at the router still encoded.
    expect(resolvePostLoginTarget(undefined, "?from=%2F%252F%252Fevil.example")).toBe("/");
    expect(resolvePostLoginTarget(undefined, "?from=%2F%255Cevil.example")).toBe("/");
  });

  it("junk and unparseable searches yield '/'", () => {
    expect(resolvePostLoginTarget(undefined, "")).toBe("/");
    expect(resolvePostLoginTarget(undefined, "?from=")).toBe("/");
    expect(resolvePostLoginTarget(undefined, "?other=%2Fchecks")).toBe("/");
  });

  it("carries legitimate deep links through both channels", () => {
    for (const input of LEGITIMATE) {
      expect(resolvePostLoginTarget(input, ""), input).toBe(input);
      expect(
        resolvePostLoginTarget(undefined, `?from=${encodeURIComponent(input)}`),
        input,
      ).toBe(input);
    }
  });
});
