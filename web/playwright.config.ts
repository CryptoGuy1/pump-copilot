import { defineConfig, devices } from "@playwright/test";

// E2E_WEB=preview measures the production build (vite preview) instead of the dev server.
const preview = process.env.E2E_WEB === "preview";
const web = "http://127.0.0.1:5174";

export default defineConfig({
  testDir: "e2e",
  timeout: 300_000,
  expect: { timeout: 15_000 },
  workers: 1,
  fullyParallel: false,
  reporter: [["list"]],
  use: { baseURL: web, trace: "retain-on-failure" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    { command: "bash e2e/backend.sh", url: "http://127.0.0.1:8001/api/health",
      timeout: 600_000, reuseExistingServer: false, stdout: "pipe", stderr: "pipe" },
    { command: preview ? "npm run build && npm run preview" : "npm run dev",
      url: web, timeout: 120_000, reuseExistingServer: false,
      env: { PUMPCOPILOT_API: "http://127.0.0.1:8001", PUMPCOPILOT_WEB_PORT: "5174" } },
  ],
});
