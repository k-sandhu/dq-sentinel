/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// API requests are proxied in dev; in production nginx (or any reverse proxy)
// must route /api to the backend. Override target with VITE_API_PROXY.
export default defineConfig(({ mode }) => ({
  plugins: [react()],
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
  build: { outDir: "dist", sourcemap: mode !== "production" },
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
