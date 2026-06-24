import { defineConfig } from "@playwright/test";

// Local smoke ONLY — intentionally NOT wired into required CI: the browser download
// plus a running backend make it a poor fit for the gate (deep-dive note + #170).
// Run with: npx playwright install chromium && npm run test:e2e
// Requires the dev API on :8000 with the seeded admin, or mock /api via page.route.
export default defineConfig({
  testDir: "./e2e",
  use: { baseURL: "http://localhost:5173" },
  webServer: {
    command: "npm run dev",
    url: "http://localhost:5173",
    reuseExistingServer: true,
    timeout: 60_000,
  },
});
