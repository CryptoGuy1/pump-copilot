/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The browser talks to /api on the dev server, which proxies to the API on 127.0.0.1.
// PUMPCOPILOT_API picks another API (the end-to-end test runs its own on port 8001).
const api = process.env.PUMPCOPILOT_API ?? "http://127.0.0.1:8000";
const port = Number(process.env.PUMPCOPILOT_WEB_PORT ?? 5173);
const proxy = { "/api": { target: api, changeOrigin: false } };

export default defineConfig({
  plugins: [react()],
  server: { host: "127.0.0.1", port, strictPort: true, proxy },
  preview: { host: "127.0.0.1", port, strictPort: true, proxy },
  test: { environment: "jsdom", include: ["src/**/*.test.{ts,tsx}"] },
});
