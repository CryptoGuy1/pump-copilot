import { defineConfig, devices } from "@playwright/test";

// Step 7b: the static snapshot as GitHub Pages would serve it. Builds the snapshot site and
// serves it under /pump-copilot/ with Pages' 404.html behaviour (scripts/serve-pages.mjs). No
// API runs: any request to one would fail the test.
// `npx playwright test -c playwright.snapshot.config.ts`
const port = 5176;

export default defineConfig({
  testDir: "snapshot-e2e",
  timeout: 120_000,
  expect: { timeout: 15_000 },
  workers: 1,
  reporter: [["list"]],
  use: { baseURL: `http://127.0.0.1:${port}/pump-copilot/` },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: "npm run build:snapshot && node scripts/serve-pages.mjs",
    url: `http://127.0.0.1:${port}/pump-copilot/`, reuseExistingServer: false, timeout: 180_000,
    env: { PUMPCOPILOT_PAGES_PORT: String(port) },
  },
});
