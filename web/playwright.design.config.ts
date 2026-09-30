import { defineConfig, devices } from "@playwright/test";

// Step 5b design checks (stage 1: design/stage1.spec.ts): screenshots and axe on the real
// dev database through the API on 127.0.0.1:8000 (no mock data). `npx playwright test -c
// playwright.design.config.ts`
const web = "http://127.0.0.1:5175";

export default defineConfig({
  testDir: "design",
  timeout: 120_000,
  expect: { timeout: 20_000 },
  workers: 1,
  reporter: [["list"]],
  use: { baseURL: web },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    { command: "cd .. && .venv/bin/pumpcopilot api --port 8000 --no-dotenv",
      url: "http://127.0.0.1:8000/api/health", reuseExistingServer: true, timeout: 120_000 },
    { command: "npm run dev", url: web, reuseExistingServer: false, timeout: 120_000,
      env: { PUMPCOPILOT_API: "http://127.0.0.1:8000", PUMPCOPILOT_WEB_PORT: "5175" } },
  ],
});
