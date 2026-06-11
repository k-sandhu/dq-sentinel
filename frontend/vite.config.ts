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
}));
