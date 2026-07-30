/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vite";

/**
 * Dependency-free bundle analyzer (#313). `ANALYZE=1 npm run build` prints, per
 * emitted chunk, the modules that dominate it using rollup's own accounting
 * (`renderedLength` = bytes that module contributed to the chunk after tree
 * shaking, before minification). A visualizer package would do the same job but
 * would be a new dev dependency for a number we only need occasionally.
 */
function bundleReport(): Plugin {
  return {
    name: "dq-bundle-report",
    apply: "build",
    generateBundle(_options, bundle) {
      const lines: string[] = [];
      const chunks = Object.values(bundle)
        .filter((c) => c.type === "chunk")
        .sort((a, b) => b.code.length - a.code.length);
      for (const chunk of chunks) {
        lines.push(`\n=== ${chunk.fileName} — ${(chunk.code.length / 1024).toFixed(1)} kB minified`);
        const groups = new Map<string, number>();
        for (const [id, info] of Object.entries(chunk.modules)) {
          const m = id.replace(/\\/g, "/").match(/node_modules\/((?:@[^/]+\/)?[^/]+)/);
          const key = m ? `node_modules/${m[1]}` : id.replace(/\\/g, "/").split("/src/")[1] || id;
          groups.set(key, (groups.get(key) ?? 0) + info.renderedLength);
        }
        for (const [key, bytes] of [...groups].sort((a, b) => b[1] - a[1]).slice(0, 18)) {
          lines.push(`  ${(bytes / 1024).toFixed(1).padStart(8)} kB  ${key}`);
        }
      }
      // eslint-disable-next-line no-console
      console.log(lines.join("\n"));
    },
  };
}

/**
 * Vendor chunking (#280, corrected and extended in #313).
 *
 * The *function* form, not the object form. The object form claims a listed
 * package plus its whole dependency graph, and when two groups' graphs overlap
 * the winner is not the order you wrote them in: `react-dom`'s implementation
 * module was being claimed by the `recharts` traversal, so `vendor-react`
 * imported `vendor-charts` and every route — charting or not — pulled 446 kB of
 * charting library on the critical path. Here the first matching rule wins,
 * per module, deterministically.
 *
 * Only packages that a group owns *exclusively* are pinned. Anything unmatched
 * is left to rollup's automatic chunking, which groups a module by the set of
 * routes that can reach it; that is what keeps the lodash/d3 subsets used by
 * dagre apart from the ones used by recharts instead of welding the lineage
 * graph to the charting bundle. A new transitive dependency therefore degrades
 * into an extra small sibling chunk, never into a wrong import edge.
 *
 * Verify a change here with `ANALYZE=1 npm run build` and by checking which
 * chunks `dist/index.html` ends up preloading.
 */
function vendorChunk(id: string): string | undefined {
  // Rollup's synthetic CJS interop helpers. Left unpinned they settle in
  // whichever vendor chunk happens to claim them and quietly couple every other
  // chunk holding a CommonJS package to it. vendor-react is the one chunk
  // everything already loads, so parking them there costs nothing.
  if (id.includes("commonjsHelpers")) return "vendor-react";

  // Greedy prefix so a nested node_modules/<a>/node_modules/<b> reports <b>.
  const pkg = /.*\/node_modules\/((?:@[^/]+\/)?[^/]+)\//.exec(id.replace(/\\/g, "/"))?.[1];
  if (!pkg) return undefined; // app code — rollup splits it per route

  // Same story as the CJS helpers: a few hundred bytes of Babel transpilation
  // helpers (`_extends`, `_objectWithoutPropertiesLoose`) shared by every
  // transpiled package. Unpinned, they were landing in vendor-charts and making
  // the CodeMirror chunk import the whole charting bundle for two functions.
  if (pkg === "@babel/runtime") return "vendor-react";

  // Matched first so nothing downstream can steal the renderer: on the critical
  // path of every route and near-frozen between deploys, so worth its own
  // long-lived cache entry.
  if (["react", "react-dom", "scheduler", "react-router", "cookie", "set-cookie-parser"].includes(pkg))
    return "vendor-react";
  if (pkg === "@tanstack/react-query" || pkg === "@tanstack/query-core") return "vendor-query";

  // The utility layer recharts and dagre/@xyflow both draw on. Pinned to its
  // own chunk precisely *because* it is shared: left to fall out naturally the
  // shared half lands inside whichever big chunk claims it first, and the
  // lineage graph ends up importing the whole charting bundle for ~70 lodash
  // helpers.
  if (pkg === "lodash" || pkg.startsWith("d3-")) return "vendor-utils";

  // Charting. Loaded only by the lazy routes that chart (Home, check detail,
  // dashboards, dataset detail, workbench, assistant).
  if (
    ["recharts", "recharts-scale", "decimal.js-light", "react-smooth", "fast-equals", "eventemitter3"].includes(pkg) ||
    ["react-is", "prop-types", "object-assign"].includes(pkg)
  )
    return "vendor-charts";

  // Workbench-only vendors: nothing outside pages/WorkbenchPage.tsx imports
  // them, so they never reach the critical path. Split three ways so the lazy
  // Workbench payload stays under the warning threshold and an app deploy can
  // reuse the cached editor/parser bytes.
  if (pkg.startsWith("@codemirror/") || pkg.startsWith("@lezer/") || pkg.startsWith("@uiw/") || ["style-mod", "crelt", "w3c-keyname", "@marijn/find-cluster-break"].includes(pkg))
    return "vendor-editor";
  if (pkg === "sql-formatter" || pkg === "nearley") return "vendor-sql-format";
  if (pkg === "@tanstack/react-table" || pkg === "@tanstack/table-core") return "vendor-table";

  return undefined;
}

// API requests are proxied in dev; in production nginx (or any reverse proxy)
// must route /api to the backend. Override target with VITE_API_PROXY.
export default defineConfig(({ mode }) => ({
  plugins: [react(), ...(process.env.ANALYZE ? [bundleReport()] : [])],
  server: {
    port: Number(process.env.PORT) || 5173,
    proxy: {
      "/api": {
        // 127.0.0.1, not localhost: with the docker demo stack up, localhost can
        // resolve to ::1 where docker's old api answers instead of dev uvicorn.
        target: process.env.VITE_API_PROXY ?? "http://127.0.0.1:8000",
        changeOrigin: true,
        ws: true, // assistant chat WebSocket
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: mode !== "production",
    rollupOptions: {
      output: {
        // Split the biggest stable vendors out of the entry chunk (it exceeded
        // 500 kB): they download in parallel and, being content-hashed, stay
        // browser-cached across app deploys that don't bump them. See
        // vendorChunk() above for why this is the function form.
        manualChunks: vendorChunk,
      },
    },
  },
  test: {
    // jsdom so component tests (React Testing Library) can render; pure-logic tests
    // run here too. setup registers jest-dom matchers + per-test cleanup.
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    css: false,
    // Unit/component tests live under src/. Playwright e2e specs live in e2e/ and run
    // via `npm run test:e2e`, NOT vitest — so they're excluded here.
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
  },
}));
