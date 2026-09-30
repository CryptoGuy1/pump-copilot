import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

const OUT = resolve(import.meta.dirname, "../../reports/design/stage0");
const THEMES = ["industrial", "aurora", "daylight"];
const VIEWPORTS = { desktop: { width: 1440, height: 1000 }, mobile: { width: 390, height: 844 } };
const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];

mkdirSync(OUT, { recursive: true });

for (const theme of THEMES) {
  for (const [vp, size] of Object.entries(VIEWPORTS)) {
    test(`fleet overview, ${theme}, ${vp}`, async ({ page }) => {
      await page.setViewportSize(size);
      await page.goto(`/?theme=${theme}`);
      await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
      // real pumps, and a real synthetic replay session from the database
      await expect(page.getByTestId(/^pump-/)).not.toHaveCount(0);
      const syn = page.getByTestId(/^synthetic-session-/).first();
      await expect(syn).toBeVisible();
      await expect(syn.getByTestId("synthetic-label").first()).toBeVisible();
      await expect(page.locator(".spark-loading")).toHaveCount(0);
      await expect(page.locator(".sparkline")).not.toHaveCount(0);
      await page.evaluate(() => document.fonts.ready);
      const fonts = await page.evaluate(() =>
        [...document.fonts].filter((f) => f.status === "loaded").map((f) => f.family));
      expect(fonts.some((f) => f.includes("IBM Plex Sans"))).toBe(true);
      expect(fonts.some((f) => f.includes("IBM Plex Mono"))).toBe(true);
      await page.screenshot({ path: `${OUT}/${theme}-${vp}.png`, fullPage: true });

      const axe = await new AxeBuilder({ page }).withTags(TAGS).analyze();
      const brief = (xs: typeof axe.violations) => xs.map((v) => ({
        id: v.id, impact: v.impact, help: v.help,
        nodes: v.nodes.map((n) => ({ target: n.target.join(" "),
                                     summary: n.failureSummary })) }));
      writeFileSync(`${OUT}/axe-${theme}-${vp}.json`, JSON.stringify({
        theme, viewport: vp, url: page.url(), tags: TAGS,
        passes: axe.passes.length, violations: brief(axe.violations),
        incomplete: brief(axe.incomplete) }, null, 2));
      expect.soft(axe.violations.map((v) => v.id)).toEqual([]);
    });
  }
}
