import { defineConfig, devices } from "@playwright/test";

// Step 5b design checks (design/stage2b.spec.ts): screenshots and axe on the real dev
// database (no mock data). The run starts its own API on 127.0.0.1:8002, never reuses one,
// with the Anthropic key emptied and .env not loaded, so no design run can reach the real
// model (tests/test_api.py guards this). `npx playwright test -c playwright.design.config.ts`
const web = "http://127.0.0.1:5175";
const api = "http://127.0.0.1:8002";

export default defineConfig({
  testDir: "design",
  timeout: 120_000,
  expect: { timeout: 20_000 },
  workers: 1,
  reporter: [["list"]],
  use: { baseURL: web },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    { command: "cd .. && .venv/bin/pumpcopilot api --port 8002 --no-dotenv",
      url: `${api}/api/health`, reuseExistingServer: false, timeout: 120_000,
      env: { ANTHROPIC_API_KEY: "" } },
    { command: "npm run dev", url: web, reuseExistingServer: false, timeout: 120_000,
      env: { PUMPCOPILOT_API: api, PUMPCOPILOT_WEB_PORT: "5175" } },
  ],
});
