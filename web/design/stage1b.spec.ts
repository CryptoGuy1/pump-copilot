import AxeBuilder from "@axe-core/playwright";
import { expect, type Page, test } from "@playwright/test";
import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

// Step 5b stage 1b: the component gallery (/design) and the fleet overview in all three
// themes, desktop and mobile, on the real dev database (no mock data): screenshots, axe
// (WCAG 2.x A/AA), and the shell's promises (focus, current page, remembered theme,
// responsive layout, reduced motion).
const OUT = resolve(import.meta.dirname, "../../reports/design/stage1b");
const THEMES = ["industrial", "aurora", "daylight"];
const VIEWPORTS = { desktop: { width: 1440, height: 1000 }, mobile: { width: 390, height: 844 } };
const PAGES = { fleet: "/", design: "/design" };
const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];
mkdirSync(OUT, { recursive: true });

async function ready(page: Page, name: string) {
  await page.evaluate(() => document.fonts.ready);
  // nothing still loading (the gallery's skeleton specimen is labelled as an example)
  await expect(page.locator('[data-testid="skeleton"]:not([aria-label="example loading"])'))
    .toHaveCount(0);
  await expect(page.locator(".spark-loading")).toHaveCount(0);
  if (name === "fleet") {
    await expect(page.getByTestId(/^pump-/)).not.toHaveCount(0);
    // the fleet grid is real pumps only; synthetic replays have their own section below
    await expect(page.getByTestId("real-fleet").getByTestId(/^synthetic-session-/)).toHaveCount(0);
    await expect(page.getByTestId("real-fleet").getByTestId("synthetic-label")).toHaveCount(0);
    const syn = page.getByTestId("synthetic-scenarios").getByTestId(/^synthetic-session-/).first();
    await expect(syn.getByTestId("synthetic-label").first()).toBeVisible();  // a real one
    // badge hierarchy: the full state badge once per card, in its header
    for (const card of await page.locator('[data-testid^="pump-"], [data-testid^="synthetic-session-"]').all()) {
      await expect(card.locator(".state")).toHaveCount(1);
      await expect(card.locator(".card-head .state, header .state")).toHaveCount(1);
      await expect(card.getByTestId("provenance-line")).toHaveCount(1);  // one quiet line
    }
    // one tile style: only the review tile takes the review colour, the synthetic its stripe
    await expect(page.getByTestId("tile-review")).toHaveCount(1);
    await expect(page.getByTestId("tile-synthetic")).toHaveCount(1);
    await expect(page.getByTestId("tile-plain")).toHaveCount(2);
    // signal names, with the technical id in the tooltip
    const sig = page.locator(".signal-name").first();
    await expect(sig).toHaveAttribute("title", /^[a-z_]+$/);
    expect(await sig.textContent()).not.toMatch(/_/);
    // the legend: a toggle on small screens
    const legend = page.getByTestId("state-legend");
    if ((page.viewportSize()?.width ?? 0) <= 720)
      await expect(legend.locator("summary")).toHaveText("What do these mean?");
    await expect(page.getByTestId("live-status")).toHaveText("Live");
    await expect(page.getByTestId("page-provenance")).toContainText("Real CIRA data +");
  } else {
    await expect(page.getByRole("heading", { name: "Components" })).toBeVisible();
  }
  await expect(page.getByTestId("page-provenance")).toBeVisible();
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
        // responsive: nothing wider than the screen
        expect(await page.evaluate(() => document.documentElement.scrollWidth))
          .toBeLessThanOrEqual(size.width);
        await page.screenshot({ path: `${OUT}/${name}-${theme}-${vp}.png`, fullPage: true });
        await axe(page, `axe-${name}-${theme}-${vp}.json`, { page: name, theme, viewport: vp });
        if (name === "design" && vp === "desktop") {  // the three themes side by side
          await page.getByRole("tab", { name: "All three themes" }).click();
          await expect(page.locator(".theme-scope")).toHaveCount(3);
          await expect(page.locator(".theme-scope .card")).toHaveCount(3);
          await page.screenshot({ path: `${OUT}/design-all-themes-${theme}-desktop.png`,
                                  fullPage: true });
          await axe(page, `axe-design-all-themes-${theme}.json`,
                    { page: "design, all three themes", theme, viewport: vp });
        }
      });
    }
  }
}

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
