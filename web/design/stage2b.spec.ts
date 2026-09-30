import AxeBuilder from "@axe-core/playwright";
import { expect, type Page, test } from "@playwright/test";
import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

// Step 5b stage 2b: the fleet, the asset-day view (in a replay) and case detail, in all three
// themes, desktop and mobile, on the real dev database (no mock data): screenshots, axe
// (WCAG 2.x A/AA) and the behaviour of the charts, the replay context and the assistant.
const OUT = resolve(import.meta.dirname, "../../reports/design/stage2b");
const THEMES = ["industrial", "aurora", "daylight"];
const VIEWPORTS = { desktop: { width: 1440, height: 1000 }, mobile: { width: 390, height: 844 } };
// replay #3 is a real synthetic session (B_stuck_pressure) paused at 10:15; case #1 is a real
// case on B, 30 October
const PAGES = { fleet: "/", "asset-day": "/assets/cira-pump-B/2024-10-30?session=3",
                case: "/cases/1" };
const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];
mkdirSync(OUT, { recursive: true });

async function ready(page: Page, name: string) {
  await page.evaluate(() => document.fonts.ready);
  await expect(page.getByTestId("skeleton")).toHaveCount(0);
  await expect(page.locator(".spark-loading")).toHaveCount(0);
  if (name === "fleet") {
    await expect(page.getByTestId(/^pump-/)).not.toHaveCount(0);
    await expect(page.getByTestId("open-cases").locator("tr")).not.toHaveCount(0);
    // case status as a neutral pill, not a state badge
    await expect(page.getByTestId("open-cases").locator(".status-pill").first()).toBeVisible();
    await expect(page.getByTestId("open-cases").locator(".state")).toHaveCount(0);
    await expect(page.locator(".day-link").first()).toBeVisible();
  } else if (name === "asset-day") {
    await expect(page.getByTestId("chart")).not.toHaveCount(0);
    await expect(page.getByRole("list", { name: "Operating state over the day" })).toBeVisible();
    // "not yet replayed": once, inside the grey area of the top chart
    await expect(page.locator('[data-testid="not-yet"]:visible')).toHaveCount(1);
    await expect(page.getByTestId("synthetic-label").first()).toBeVisible();  // page banner
    await expect(page.locator(".tchart .synthetic")).toHaveCount(0);        // no per-chart badge
    if ((page.viewportSize()?.width ?? 0) > 720)  // before the first scored window
      await expect(page.locator(".forming-label:visible").first()).toHaveText("Baseline forming");
    else  // on phones the "not yet replayed" label sits in the top chart's header
      await expect(page.locator(".tchart-head [data-testid=not-yet]")).toContainText("after 10:15");
  } else {
    await expect(page.getByRole("list", { name: "Case status" })).toBeVisible();
    await expect(page.locator(".case-detail .eyebrow").first()).toContainText("run 1");
    await expect(page.getByRole("list", { name: "Evidence over time" })).toBeVisible();
    await expect(page.getByTestId("chart")).not.toHaveCount(0);
  }
  await expect(page.getByTestId("page-provenance")).toBeVisible();
  await page.waitForTimeout(300);  // charts settle their size
}

async function axe(page: Page, file: string, meta: object) {
  // the whole page in view, so no element is cut by the viewport edge (axe cannot measure
  // the contrast of text it sees only partly)
  const size = page.viewportSize()!;
  const height = await page.evaluate(() => document.documentElement.scrollHeight);
  await page.setViewportSize({ width: size.width, height });
  const r = await new AxeBuilder({ page }).withTags(TAGS).analyze();
  await page.setViewportSize(size);
  const brief = (xs: typeof r.violations) => xs.map((v) => ({
    id: v.id, impact: v.impact, help: v.help,
    nodes: v.nodes.map((n) => ({ target: n.target.join(" "), summary: n.failureSummary })) }));
  writeFileSync(`${OUT}/${file}`, JSON.stringify({ ...meta, url: page.url(), tags: TAGS,
    passes: r.passes.length, violations: brief(r.violations), incomplete: brief(r.incomplete) },
    null, 2));
  expect.soft(r.violations.map((v) => v.id), file).toEqual([]);
  expect.soft(r.incomplete.map((v) => v.id), `${file} (unmeasured)`).toEqual([]);
}

for (const theme of THEMES) {
  for (const [vp, size] of Object.entries(VIEWPORTS)) {
    for (const [name, path] of Object.entries(PAGES)) {
      test(`${name}, ${theme}, ${vp}`, async ({ page }) => {
        await page.setViewportSize(size);
        await page.goto(`${path}${path.includes("?") ? "&" : "?"}theme=${theme}`);
        await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
        await ready(page, name);
        expect(await page.evaluate(() => document.documentElement.scrollWidth))
          .toBeLessThanOrEqual(size.width);  // nothing wider than the screen
        await page.screenshot({ path: `${OUT}/${name}-${theme}-${vp}.png`, fullPage: true });
        await axe(page, `axe-${name}-${theme}-${vp}.json`, { page: name, theme, viewport: vp });
      });
    }
  }
}

test("asset day: shared cursor readouts, zoom and reset, the full day, a real replay",
     async ({ page }) => {
  await page.setViewportSize(VIEWPORTS.desktop);
  await page.goto(`${PAGES["asset-day"]}&theme=industrial`);
  await ready(page, "asset-day");
  const charts = page.getByTestId("chart");
  // hover one chart: every chart shows its readout at the same time (a shared cursor)
  const box = (await charts.first().boundingBox())!;
  await page.mouse.move(box.x + box.width * 0.35, box.y + box.height / 2);
  const readouts = page.locator(".tchart-readout");
  await expect(readouts.first()).toContainText("UTC");
  await expect(readouts.nth(1)).toContainText("UTC");
  expect(await readouts.first().textContent()).toEqual(expect.stringMatching(/\d{2}:\d{2}:\d{2} UTC/));
  await page.screenshot({ path: `${OUT}/asset-day-readout-industrial-desktop.png` });
  // zoom in, then reset
  const tools = page.getByRole("toolbar", { name: /zoom and pan/ });
  const before = await tools.locator(".range").textContent();
  await tools.getByRole("button", { name: "Zoom in" }).click();
  await expect(tools.locator(".range")).not.toHaveText(before!);
  await tools.getByRole("button", { name: "Pan later" }).click();
  await tools.getByRole("button", { name: "Reset" }).click();
  await expect(tools.locator(".range")).toHaveText(before!);
  // by default the run up to the cursor; "Show the full day" shows 08:15-16:29
  const range = tools.locator(".range");
  await expect(range).not.toHaveText("08:15–16:29 UTC");
  await expect(range).toContainText("10:");  // ends a little after the 10:15 cursor
  // raw signals: hidden beyond the cursor until "Show the full day"
  await page.getByRole("tab", { name: /Raw signals/ }).click();
  await expect(page.locator('[data-testid="not-yet"]:visible')).toHaveText("Not yet replayed");
  await page.getByLabel("Show the full day").check();
  await expect(range).toHaveText("08:15–16:29 UTC");
  await expect(page.locator('[data-testid="not-yet"]:visible')).toContainText("shown grey");
  // disabled controls look disabled (a dashed edge), not only paler
  await expect(tools.getByRole("button", { name: "Pan later" })).toHaveCSS("border-top-style", "dashed");
  await page.screenshot({ path: `${OUT}/asset-day-full-day-industrial-desktop.png`, fullPage: true });
  await axe(page, "axe-asset-day-full-day-industrial-desktop.json",
            { page: "asset-day, raw, full day", theme: "industrial", viewport: "desktop" });
  // a real, completed replay: no cursor line, no SYNTHETIC
  await page.goto("/assets/cira-pump-B/2024-10-30?session=1&theme=industrial");
  await expect(page.getByTestId("chart")).not.toHaveCount(0);
  await expect(page.getByTestId("synthetic-label")).toHaveCount(0);
  await page.waitForTimeout(300);
  await page.screenshot({ path: `${OUT}/asset-day-real-industrial-desktop.png`, fullPage: true });
});

test("mobile: a tap moves the cursor and shows the readouts", async ({ browser }) => {
  const ctx = await browser.newContext({ viewport: VIEWPORTS.mobile, hasTouch: true,
                                         isMobile: true });
  const page = await ctx.newPage();
  await page.goto(`${PAGES["asset-day"]}&theme=industrial`);
  await ready(page, "asset-day");
  // the first chart appears within the first mobile screen; the legend folds away
  const first = (await page.getByTestId("chart").first().boundingBox())!;
  expect(first.y).toBeLessThan(VIEWPORTS.mobile.height - 60);
  await expect(page.locator(".legend-fold > summary")).toHaveText("Legend");
  await page.screenshot({ path: `${OUT}/asset-day-first-screen-industrial-mobile.png` });
  const chart = page.getByTestId("chart").first();
  await chart.scrollIntoViewIfNeeded();
  const box = (await chart.boundingBox())!;
  await page.touchscreen.tap(box.x + box.width * 0.4, box.y + box.height / 2);
  await expect(page.locator(".tchart-readout").first()).toContainText("UTC");
  await page.screenshot({ path: `${OUT}/asset-day-touch-industrial-mobile.png` });
  await ctx.close();
});

test("case detail: the summary at once, the checked answer after; evidence IDs highlight",
     async ({ page }) => {
  await page.setViewportSize(VIEWPORTS.desktop);
  // hold the checked answer back a little so the summary-first state can be seen; the
  // answer itself is the real one from the API
  await page.route("**/api/cases/*/assistant", async (route) => {
    if (route.request().postDataJSON()?.provider === "auto")
      await new Promise((r) => setTimeout(r, 2500));
    await route.continue();
  });
  await page.goto("/cases/1?theme=industrial");
  await ready(page, "case");
  await expect(page.getByRole("group", { name: "Suggested questions" }).getByRole("button"))
    .toHaveText(["Summarize this case", "Which signal drove it?", "What should I check next?"]);
  await page.getByRole("button", { name: "Summarize this case" }).click();
  await expect(page.getByTestId("assistant-badge")).toHaveText("Evidence summary");
  await expect(page.getByTestId("assistant-working")).toBeVisible();
  await page.getByTestId("assistant").scrollIntoViewIfNeeded();
  await page.screenshot({ path: `${OUT}/case-assistant-working-industrial-desktop.png` });
  await expect(page.getByTestId("assistant-working")).toHaveCount(0, { timeout: 30_000 });
  await expect(page.getByTestId("assistant-badge")).toBeVisible();  // says which it shows
  await expect(page.getByTestId("stored-units")).toContainText("vibration in m/s; the charts show mm/s");
  await page.getByTestId("assistant-check").locator("summary").click();
  await page.getByTestId("ref-E2").first().click();
  await expect(page.locator("[data-highlighted=true]").first()).toBeVisible();
  await page.screenshot({ path: `${OUT}/case-assistant-answer-industrial-desktop.png`,
                          fullPage: true });
  // the evidence timeline highlights too
  await page.getByRole("list", { name: "Evidence over time" }).getByRole("button").first().click();
  await expect(page.getByTestId("highlight-note")).toBeVisible();
  // a synthetic case
  await page.goto("/cases/2?theme=industrial");
  await ready(page, "case");
  await expect(page.getByTestId("synthetic-label").first()).toBeVisible();
  await page.screenshot({ path: `${OUT}/case-synthetic-industrial-desktop.png`, fullPage: true });
});

test("the shell: current page, visible focus, remembered theme, mobile menu", async ({ page }) => {
  await page.goto("/assumptions?theme=aurora");
  const nav = page.getByRole("navigation", { name: "Main" });
  await expect(nav.getByRole("link", { name: "Assumptions", exact: true }))
    .toHaveAttribute("aria-current", "page");
  await expect(nav.getByRole("link", { name: "Fleet", exact: true }))
    .not.toHaveAttribute("aria-current", "page");
  // the gallery is linked from the footer, not the navigation
  await expect(nav.getByRole("link", { name: /design/i })).toHaveCount(0);
  await page.getByRole("contentinfo").getByRole("link", { name: "Design system" }).click();
  await expect(page.getByRole("heading", { name: "Components" })).toBeVisible();
  await page.goto("/cases");  // no ?theme=: the choice is remembered
  await expect(page.locator("html")).toHaveAttribute("data-theme", "aurora");
  await expect(page.getByTestId("page-provenance")).toBeVisible();  // on every page
  // keyboard: the skip link first, then the navigation, each with a visible outline
  await page.keyboard.press("Tab");
  const skip = page.getByRole("link", { name: "Skip to content" });
  await expect(skip).toBeFocused();
  await expect(skip).toBeInViewport();
  await page.keyboard.press("Tab");
  const outline = await page.evaluate(() => {
    const s = getComputedStyle(document.activeElement!);
    return { style: s.outlineStyle, width: parseFloat(s.outlineWidth) };
  });
  expect(outline.style).toBe("solid");
  expect(outline.width).toBeGreaterThanOrEqual(2);
  // mobile: the navigation folds into a menu
  await page.setViewportSize(VIEWPORTS.mobile);
  await expect(page.getByRole("link", { name: "Replay", exact: true })).toBeHidden();
  await page.getByRole("button", { name: "Menu" }).click();
  await expect(page.getByRole("link", { name: "Replay", exact: true })).toBeVisible();
  await page.getByRole("link", { name: "Replay", exact: true }).click();
  await expect(page).toHaveURL(/\/replay$/);
  await expect(page.getByRole("link", { name: "Replay", exact: true })).toBeHidden();
});

test("reduced motion turns transitions off", async ({ browser }) => {
  const ctx = await browser.newContext({ reducedMotion: "reduce" });
  const page = await ctx.newPage();
  await page.goto("/design");
  const tokens = await page.evaluate(() => {
    const s = getComputedStyle(document.documentElement);
    return ["--motion-fast", "--motion-base", "--motion-slow"].map((t) =>
      s.getPropertyValue(t).trim());
  });
  expect(tokens).toEqual(["0ms", "0ms", "0ms"]);
  await expect(page.locator(".enter").first()).toHaveCSS("animation-name", "none");
  await ctx.close();
});
