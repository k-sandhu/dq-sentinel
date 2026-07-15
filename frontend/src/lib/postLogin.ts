/** Post-login redirect target (UX benchmark P1: deep links must survive login).
 *
 * The unauthenticated catch-all redirect stores the intended location in
 * `history.state.from`; after auth we send the user there instead of "/".
 * `safeInternalPath` accepts only same-app absolute paths so a crafted link
 * can never turn the login flow into an open redirect:
 *   - must start with exactly one "/" ("//host" is a scheme-relative URL);
 *   - never back to "/login" (would loop).
 */
export function safeInternalPath(from: unknown): string {
  if (typeof from !== "string") return "/";
  if (!from.startsWith("/") || from.startsWith("//")) return "/";
  if (from === "/login" || from.startsWith("/login?")) return "/";
  return from;
}
