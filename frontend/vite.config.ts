import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// API requests are proxied in dev; in production nginx (or any reverse proxy)
// must route /api to the backend. Override target with VITE_API_PROXY.
export default defineConfig(({ mode }) => ({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.VITE_API_PROXY ?? "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
  build: { outDir: "dist", sourcemap: mode !== "production" },
}));
