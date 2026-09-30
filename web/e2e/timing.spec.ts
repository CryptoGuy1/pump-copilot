import { writeFileSync } from "node:fs";
import { expect, test, type Page } from "@playwright/test";

// One end-to-end measurement per page, over HTTP through the web server and the API:
// page load (navigation start to load event) and time to the first rendered chart.
async function measure(page: Page, path: string, chart: boolean) {
  await page.goto(path);
  if (chart) await expect(page.getByTestId("chart").first()).toBeVisible({ timeout: 60_000 });
  // the fleet overview's pump cards (stage 0 replaced its table)
  else await expect(page.getByTestId(/^pump-/).first()).toBeVisible({ timeout: 60_000 });
  return page.evaluate(() => {
    const nav = performance.getEntriesByType("navigation")[0] as PerformanceNavigationTiming;
    return { load_ms: Math.round(nav.loadEventEnd), dom_content_loaded_ms:
               Math.round(nav.domContentLoadedEventEnd),
             first_chart_ms: window.__firstChartAt === undefined ? null
               : Math.round(window.__firstChartAt) };
  });
}

test("page load and time to first chart", async ({ browser, request }) => {
  const cases = await (await request.get("/api/cases?asset_id=cira-pump-B")).json();
  const caseId = cases.cases[0]?.case_id;
  const targets: [string, string, boolean][] = [
    ["fleet overview", "/", false],
    ["asset-day B 2024-10-30", "/assets/cira-pump-B/2024-10-30", true],
    ...(caseId ? [[`case detail #${caseId}`, `/cases/${caseId}`, true] as [string, string,
                                                                              boolean]] : []),
  ];
  const out: Record<string, unknown> = { web: process.env.E2E_WEB ?? "dev" };
  for (const [name, path, chart] of targets) {
    const ctx = await browser.newContext(); // cold: nothing cached
    out[name] = await measure(await ctx.newPage(), path, chart);
    await ctx.close();
  }
  console.log(JSON.stringify(out, null, 2));
  writeFileSync(`test-results/timing-${out.web}.json`, JSON.stringify(out, null, 2));
});
