import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

/**
 * Bundle-shape regression guard (#313).
 *
 * The entry chunk is on the critical path of the first paint of every session,
 * and it grew to 901 kB because pages that drag in a charting / graph / editor
 * / markdown stack were statically imported from App.tsx. Nothing about that
 * failure is visible in a unit test of any single page, and CI does not gate on
 * bundle size — so it is asserted here, where the frontend job already runs
 * vitest immediately after `npm run build`.
 */

const SRC = path.dirname(fileURLToPath(import.meta.url));
const DIST = path.resolve(SRC, "..", "dist");

/**
 * Pages that must stay behind `lazy(() => import(...))`. Each one either pulls a
 * large third-party stack (recharts / @xyflow+dagre / CodeMirror+sql-formatter /
 * react-markdown) or is a large page most sessions never open. A plain
 * `import X from "./pages/X"` here silently welds all of that into the entry
 * chunk again.
 */
const LAZY_PAGES = [
  "AssistantPage",
  "CheckDetailPage",
  "CustomDashboardPage",
  "DatasetDetailPage",
  "DocsPage",
  "HomePage",
  "LineagePage",
  "SettingsPage",
  "WorkbenchPage",
];

describe("route code-splitting", () => {
  const app = readFileSync(path.join(SRC, "App.tsx"), "utf8");

  it.each(LAZY_PAGES)("%s is loaded lazily, not from the entry chunk", (page) => {
    expect(app).toContain(`import("./pages/${page}")`);
    expect(app).not.toMatch(new RegExp(String.raw`^\s*import\s+${page}\s+from`, "m"));
  });

  it("keeps a Suspense fallback for the lazy routes", () => {
    expect(app).toMatch(/<Suspense\s+fallback=\{<Spinner/);
  });
});

/**
 * The built-bundle assertions need `dist/`, which CI produces one step before
 * vitest runs. Locally they are skipped until someone runs `npm run build`,
 * rather than failing for a reason that has nothing to do with their change.
 */
const built = existsSync(path.join(DIST, "index.html"));

/** Chunks that must never be pulled in before the first paint. */
const OFF_CRITICAL_PATH = /vendor-charts|vendor-editor|vendor-sql-format|vendor-table|vendor-utils|Markdown|LineageGraph/;

/** Vite's own warning threshold — keep it as the per-chunk budget. */
const MAX_CHUNK_BYTES = 500 * 1024;

/**
 * Soft ceiling for everything the browser must fetch before it can render:
 * ~35% headroom over the current 436 kB so ordinary growth doesn't trip it, but
 * a 400 kB library landing back on the critical path does.
 */
const MAX_CRITICAL_PATH_BYTES = 600 * 1024;

function criticalPathChunks(): string[] {
  const html = readFileSync(path.join(DIST, "index.html"), "utf8");
  // Vite emits the entry as <script type="module" src> and every chunk in its
  // static import graph as <link rel="modulepreload">.
  return [...html.matchAll(/(?:src|href)="\/assets\/([^"]+\.js)"/g)].map((m) => m[1]);
}

describe.skipIf(!built)("production bundle budget", () => {
  it("emits no chunk over vite's 500 kB warning threshold", () => {
    const oversized = readdirSync(path.join(DIST, "assets"))
      .filter((f) => f.endsWith(".js"))
      .map((name) => [name, statSync(path.join(DIST, "assets", name)).size] as const)
      .filter(([, size]) => size > MAX_CHUNK_BYTES);
    expect(oversized).toEqual([]);
  });

  it("does not load the charting, editor or graph bundles before first paint", () => {
    expect(criticalPathChunks().filter((name) => OFF_CRITICAL_PATH.test(name))).toEqual([]);
  });

  it("keeps the critical path under the soft budget", () => {
    const total = criticalPathChunks().reduce(
      (sum, name) => sum + statSync(path.join(DIST, "assets", name)).size,
      0,
    );
    expect(total).toBeLessThan(MAX_CRITICAL_PATH_BYTES);
  });
});
