import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server proxies API/WS to the FastAPI backend.  In production the
// built assets are served by FastAPI itself (at /app), so the frontend
// always talks to its own origin and this proxy is dev-only.
//
// `base` is /app/ for production builds so the asset URLs resolve under the
// FastAPI mount point; the dev server keeps serving at / (port 5173).
export default defineConfig(({ command }) => ({
  base: command === "build" ? "/app/" : "/",
  plugins: [react()],
  server: {
    port: 5173,
    host: true,
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/ws": { target: "ws://127.0.0.1:8000", ws: true },
    },
  },
  build: { outDir: "dist", sourcemap: false },
}));
