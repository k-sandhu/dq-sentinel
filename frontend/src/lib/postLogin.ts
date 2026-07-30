/** Post-login redirect target (UX benchmark P1: deep links must survive login).
 *
 * The unauthenticated catch-all redirect stores the intended location in
 * `history.state.from`; after auth we send the user there instead of "/".
 * That value is attacker-reachable — anyone can hand an analyst a
 * `/login?from=…` link — so `safeInternalPath` is the open-redirect gate for
 * the whole login flow, and it has to defend against every spelling of
 * "authority" the WHATWG URL parser accepts, not just "//host".
 *
 * The rules, in order of how they fail (each verified against `new URL`):
 *
 *   1. A C0 control or DEL anywhere → reject. Browsers *delete* tab/CR/LF from
 *      a URL before parsing, anywhere in the string, so `"/<TAB>/evil.example"`
 *      parses as `"//evil.example"` (origin `https://evil.example`).
 *   2. Leading/trailing whitespace → reject. The parser trims it first, so
 *      `" //evil.example"` is scheme-relative.
 *   3. A backslash anywhere → reject. For special schemes the parser treats "\"
 *      as "/", which makes `"/\evil.example"`, `"\/evil.example"` and
 *      `"/\\evil.example"` all scheme-relative — this is the backslash-bypass
 *      class (CVE-2025-68470 / GHSA-wrjc-x8rr-h8h6) that the react-router
 *      advisory names, and upgrading the router does not fix *our* helper.
 *      No route in `App.tsx` contains a backslash, so a blanket reject costs
 *      nothing. (A backslash typed into a filter and round-tripped through
 *      `?from=` also falls back to "/" — a landing-page downgrade, never a
 *      redirect off-origin.)
 *   4. Must start with exactly one "/" — `"//host"`/`"///host"` are
 *      scheme-relative, and a relative path ("checks") is not an app location.
 *   5. A percent-encoded separator (%2F / %5C) in the *path* → reject. The
 *      parser does not decode these, so they are same-origin as written; the
 *      risk is a second decode downstream (react-router decodes path params;
 *      proxies and future normalisation steps decode too), after which the
 *      value becomes a real separator. No route needs an encoded separator in
 *      its path, so rejecting there is free — while rejecting them in the query
 *      or hash would break legitimate deep links (encoded filter params are
 *      everywhere in the exceptions workspace, and neither query nor hash can
 *      ever contribute to the authority).
 *   6. Never back to "/login" (would loop). Matched case-insensitively on the
 *      path only, because react-router matches routes case-insensitively.
 *   7. Finally, the authoritative gate: resolve the candidate with the same URL
 *      parser the browser uses and require the origin to come back unchanged.
 *      The string rules above are explicit and cheap; this catches whatever
 *      spelling they missed. String-shaped checks alone are what let the
 *      backslash bypass through in the first place.
 */

/** Any absolute origin works — resolution of a *relative* reference only ever
 *  keeps or changes the origin, so a genuinely internal path must resolve back
 *  to exactly this one. A fixed synthetic origin keeps the check identical in
 *  tests, in the browser and under any deploy host. */
const PROBE_ORIGIN = "https://dq-sentinel.invalid";

/** C0 controls + DEL. Tab/CR/LF are stripped by the URL parser anywhere in the
 *  input, so these must be rejected anywhere, not just at the start. */
const CONTROL_CHARS = /[\u0000-\u001f\u007f]/;

/** Percent-encoded "/" or "\" (any case). */
const ENCODED_SEPARATOR = /%(?:2f|5c)/i;

export function safeInternalPath(from: unknown): string {
  if (typeof from !== "string" || from === "") return "/";
  if (CONTROL_CHARS.test(from)) return "/";
  if (from !== from.trim()) return "/";
  if (from.includes("\\")) return "/";
  if (!from.startsWith("/") || from.startsWith("//")) return "/";

  const cut = from.search(/[?#]/);
  const path = (cut === -1 ? from : from.slice(0, cut)).toLowerCase();
  if (ENCODED_SEPARATOR.test(path)) return "/";
  if (path === "/login" || path.startsWith("/login/")) return "/";

  try {
    if (new URL(from, PROBE_ORIGIN).origin !== PROBE_ORIGIN) return "/";
  } catch {
    return "/"; // unparseable — not a location we can send anyone to
  }
  return from;
}

/** Where to land after login. Router state wins (client-side redirect);
 *  the `?from=` query is the fallback written by the API client's hard
 *  401 redirect, which cannot carry router state (codex review: expired
 *  tokens previously always landed on "/"). Both are validated. */
export function resolvePostLoginTarget(stateFrom: unknown, search: string): string {
  const fromState = safeInternalPath(stateFrom);
  if (fromState !== "/") return fromState;
  let queryFrom: string | null = null;
  try {
    queryFrom = new URLSearchParams(search).get("from");
  } catch {
    queryFrom = null;
  }
  return safeInternalPath(queryFrom);
}
