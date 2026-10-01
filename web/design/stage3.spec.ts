import AxeBuilder from "@axe-core/playwright";
import { expect, type Page, test } from "@playwright/test";
import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

// Step 5b stage 3: evaluation, About, replay and data quality in all three themes, desktop
// and mobile, on the real dev database (no mock data): screenshots and axe (WCAG 2.x A/AA),
// plus page titles, the favicon, sharing metadata and the not-found page.
const OUT = resolve(import.meta.dirname, "../../reports/design/stage3");
const THEMES = ["industrial", "aurora", "daylight"];
const VIEWPORTS = { desktop: { width: 1440, height: 1000 }, mobile: { width: 390, height: 844 } };
const PAGES = { evaluation: "/evaluation", about: "/about", replay: "/replay",
                "data-quality": "/data-quality" };
const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];
mkdirSync(OUT, { recursive: true });

async function ready(page: Page, name: string) {
  await page.evaluate(() => document.fonts.ready);
  await expect(page.getByTestId("skeleton")).toHaveCount(0);
  if (name === "evaluation") {
    await expect(page.getByRole("heading", { name: /1 · ZeMA/ })).toBeVisible();
    await expect(page.getByText("Test rig only.")).toBeVisible();  // the disclaimer first
    await expect(page.locator(".hm-cell")).toHaveCount(15);
    await expect(page.locator(".readout", { hasText: "Instructions served" })
      .locator(".readout-value")).toHaveText("0");
    await expect(page.locator(".how-to-read")).toHaveCount(3);
    await expect(page.getByText(/^Same model, same data: \d\.\d{3} on a random split, \d\.\d{3} on a chronological one\.$/)).toBeVisible();
    await expect(page.locator(".ibar-cap")).not.toHaveCount(0);  // whiskers with end caps
    await expect(page.locator(".hm-head [role=columnheader]")).toHaveText(
      ["Fault size", "small", "medium", "large"]);
    await expect(page.locator("#cira-exploratory")).toContainText("outside the protocol");
    await expect(page.getByTestId("page-provenance")).toHaveText(
      "Stored evaluation results from committed reports · nothing re-scored");
  } else if (name === "about") {
    if ((page.viewportSize()?.width ?? 0) > 720) {  // the diagram; a stacked flow on phones
      await expect(page.getByRole("region", { name: "Architecture diagram" })).toBeVisible();
      await expect(page.locator(".arch-box")).toHaveCount(10);
    } else await expect(page.locator(".arch-steps")).toHaveCount(3);
    await expect(page.locator(".sources > li")).toHaveCount(2);
    await expect(page.getByText("CC BY 4.0").first()).toBeVisible();
    await expect(page.locator(".highlights li")).toHaveCount(3);
    await expect(page.getByText(/Schütze, A\. \(2015\)/)).toBeVisible();
    await expect(page.getByText("Changes: raw data unchanged; features and scores derived.")
      .first()).toBeAttached();
    await expect(page.locator(".author-name")).toHaveText("Benjamin Nweke");
    await expect(page.getByTestId("page-provenance")).toHaveText("About this project");
    // the repository link only while the About facts give one (null while it is private)
    const about = await (await page.request.get("/api/about")).json();
    await expect(page.getByText(/^Repository:/)).toHaveCount(about.repository ? 1 : 0);
  } else if (name === "replay") {
    await expect(page.getByTestId(/^session-\d+$/)).not.toHaveCount(0);
    await expect(page.getByRole("heading", { name: "Worker" })).toBeVisible();
  } else {
    await expect(page.locator(".known-issues li")).toHaveCount(8);
    await expect(page.locator(".dq-grid .card")).not.toHaveCount(0);
    await expect(page.locator(".dq-grid .muted", { hasText: "loading" })).toHaveCount(0);
  }
  await expect(page.getByTestId("page-provenance")).toBeVisible();
  await page.waitForTimeout(200);
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
        await page.goto(`${path}?theme=${theme}`);
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

test("finishing: titles, favicon, sharing preview, not found", async ({ page, request }) => {
  const titles: [string, RegExp][] = [["/", /^Fleet overview · pump-copilot$/],
    ["/evaluation", /^Evaluation · /], ["/about", /^About · /], ["/replay", /^Replay · /],
    ["/data-quality", /^Data quality · /], ["/cases/1", /^Case #1 · /],
    ["/assets/cira-pump-B/2024-10-30", /^cira-pump-B · 2024-10-30 · /]];
  for (const [path, title] of titles) {
    await page.goto(path);
    await expect(page).toHaveTitle(title);
  }
  await expect(page.locator('link[rel="icon"]')).toHaveAttribute("href", "/favicon.svg");
  expect((await request.get("/favicon.svg")).ok()).toBe(true);
  for (const p of ["og:title", "og:description", "og:image", "og:image:alt"])
    await expect(page.locator(`meta[property="${p}"]`)).toHaveCount(1);
  await expect(page.locator('meta[name="twitter:card"]')).toHaveAttribute("content",
                                                                         "summary_large_image");
  // the sharing image: the real fleet overview, made once (kept in web/public/og.png)
  const og = resolve(import.meta.dirname, "../public/og.png");
  if (!existsSync(og)) {
    await page.setViewportSize({ width: 1200, height: 630 });
    await page.goto("/?theme=industrial");
    await expect(page.getByTestId(/^pump-/)).not.toHaveCount(0);
    await page.evaluate(() => document.fonts.ready);
    await page.waitForTimeout(500);
    await page.screenshot({ path: og });
  }
  expect((await request.get("/og.png")).ok()).toBe(true);
  // the not-found page
  await page.goto("/no-such-page?theme=industrial");
  await expect(page.getByRole("heading", { name: "Page not found" })).toBeVisible();
  await expect(page).toHaveTitle(/^Page not found · /);
  await page.screenshot({ path: `${OUT}/not-found-industrial-desktop.png` });
  await axe(page, "axe-not-found-industrial-desktop.json",
            { page: "not found", theme: "industrial", viewport: "desktop" });
  await expect(page.getByRole("navigation", { name: "Main" }).getByRole("link",
    { name: "About", exact: true })).toBeVisible();
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
