import AxeBuilder from "@axe-core/playwright";
import { expect, type Page, test } from "@playwright/test";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

// Step 7b: the static snapshot. Every route renders from a deep link; nothing is requested from
// outside the site (and nothing from an API); every action is disabled; axe passes in every
// theme. axe reports go to reports/design/snapshot/.
const OUT = resolve(import.meta.dirname, "../../reports/design/snapshot");
mkdirSync(OUT, { recursive: true });
const manifest = JSON.parse(readFileSync(resolve(import.meta.dirname,
  "../public/snapshot/manifest.json"), "utf8"));
const answers = JSON.parse(readFileSync(resolve(import.meta.dirname,
  "../public/snapshot/answers.json"), "utf8"));
const THEMES = ["industrial", "aurora", "daylight"];
const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];
const SITE = "http://127.0.0.1:5176/pump-copilot/";
const day = (iso: string) => new Date(iso).toLocaleDateString("en-GB",
  { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" });

// route, and how many "Not included in this snapshot" panels it may show
const ROUTES: [string, number][] = [
  ["", 0], ["cases", 0], ["replay", 1],  // worker health is live status: not exported
  ["evaluation", 0], ["data-quality", 0], ["assumptions", 0], ["about", 0], ["design", 0],
  ...manifest.sessions.map((s: { asset_id: string; source_day: string; session_id: number }) =>
    [`assets/${s.asset_id}/${s.source_day}?session=${s.session_id}`, 0] as [string, number]),
  ["assets/cira-pump-B/2024-10-30", 0], ["assets/cira-pump-A/2024-10-30", 0],
  ...[1, 2, 3, 4, 5].map((id) => [`cases/${id}`, 0] as [string, number]),
];
// the controls that change something, by name; each must be disabled
const ACTIONS = /^(Acknowledge|Add note|Set disposition|Close case|Start replay|start|resume|pause|rewind|Ask|Approve and save|JSON|Markdown evidence pack|Preview evidence pack)$/;

function watch(page: Page) {
  const outside: string[] = [];
  page.on("request", (r) => {
    // the snapshot files are under SITE/snapshot/; an API call would go to /api/, outside SITE
    if (!r.url().startsWith(SITE)) outside.push(r.url());
  });
  page.on("websocket", (w) => outside.push(w.url()));
  return outside;
}

async function ready(page: Page, missing: number) {
  await page.evaluate(() => document.fonts.ready);
  // (the design system gallery shows one placeholder as an example)
  await expect(page.locator('[data-testid=skeleton]:not([aria-label="example loading"])'))
    .toHaveCount(0);
  await expect(page.getByTestId("snapshot-banner")).toHaveText(
    `Static snapshot of ${day(manifest.exported_at)} · read-only · nothing is live. Run it yourself`);
  // (the design system gallery shows one error panel as an example)
  await expect(page.locator("main [data-testid=error-panel]").filter(
    { hasNotText: "Could not load the fleet" })).toHaveCount(0);
  await expect(page.getByTestId("not-in-snapshot")).toHaveCount(missing);
  await expect(page.getByTestId("live-status")).toHaveText("Snapshot");
  await expect(page.getByText(/moves live|appear live/)).toHaveCount(0);  // nothing claims to be live
}

async function actionsDisabled(page: Page) {
  // the design system gallery's specimens are examples with no effect, not actions
  for (const role of ["button", "link"] as const) {
    for (const el of await page.getByRole(role, { name: ACTIONS }).all())
      if (await el.isVisible() && !(await el.evaluate((e) => !!e.closest(".specimen"))))
        await expect(el).toBeDisabled();
  }
  for (const el of await page.locator("[data-testid=snapshot-actions]")
    .locator("button, input, select, textarea").all()) await expect(el).toBeDisabled();
}

async function axe(page: Page, file: string) {
  const size = page.viewportSize()!;
  const h = await page.evaluate(() => document.documentElement.scrollHeight);
  await page.setViewportSize({ width: size.width, height: Math.max(size.height, h) });
  const r = await new AxeBuilder({ page }).withTags(TAGS).analyze();
  await page.setViewportSize(size);
  writeFileSync(resolve(OUT, file), JSON.stringify({ violations: r.violations,
    incomplete: r.incomplete.map((x) => ({ id: x.id, nodes: x.nodes.length })) }, null, 1));
  expect(r.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target).join(" ")}`)).toEqual([]);
}

for (const [route, missing] of ROUTES) {
  test(`renders /${route} from a deep link, offline, with every action disabled`, async ({ page }) => {
    const outside = watch(page);
    await page.goto(route);
    await ready(page, missing);
    await actionsDisabled(page);
    expect(outside).toEqual([]);
  });
}

for (const theme of THEMES) {
  test(`axe passes on every route in ${theme}`, async ({ page }) => {
    // measured at rest: the gallery's motion specimen replays an arrival animation
    await page.emulateMedia({ reducedMotion: "reduce" });
    const outside = watch(page);
    for (const [route, missing] of ROUTES) {
      await page.goto(route + (route.includes("?") ? "&" : "?") + `theme=${theme}`);
      await ready(page, missing);
      await axe(page, `axe-${(route || "fleet").replace(/[/?=]/g, "_")}-${theme}.json`);
    }
    expect(outside).toEqual([]);
  });
}

test("the quick questions show the recorded answers, labelled", async ({ page }) => {
  const outside = watch(page);
  const label = `Recorded answer, ${answers.model}, ${day(answers.recorded_at)}`;
  for (const a of answers.answers) {
    await page.goto(`cases/${a.case_id}`);
    await ready(page, 0);
    await page.getByRole("button", { name: a.question, exact: true }).click();
    await expect(page.getByTestId("recorded-label")).toHaveText(label);
    const passed = answers.answers.filter((x: { response: { served: string } }) =>
      x.response.served === "assistant").length;
    await expect(page.getByTestId("recorded-summary")).toContainText(
      `${passed} of ${answers.answers.length} recorded answers passed the checker`);
    await expect(page.getByTestId("assistant-badge")).toHaveText(a.response.label);
    await expect(page.getByTestId("snapshot-actions").first()).toContainText(
      "Static snapshot: actions are disabled");
  }
  await axe(page, "axe-recorded-answer-industrial.json");
  expect(outside).toEqual([]);
});

test("what was not exported says so, and unknown addresses reach the not-found page", async ({ page }) => {
  const outside = watch(page);
  await page.goto("assets/cira-pump-A/2024-06-11");
  await expect(page.getByTestId("not-in-snapshot").first()).toBeVisible();
  await expect(page.getByText("Not included in this snapshot").first()).toBeVisible();
  const r = await page.goto("no/such/page");
  expect(r?.status()).toBe(404);  // as on GitHub Pages: 404.html, then the app's own page
  await expect(page.getByRole("heading", { name: "Page not found" })).toBeVisible();
  await expect(page).toHaveTitle("Page not found · pump-copilot");
  expect(outside).toEqual([]);
});

test("the sharing preview uses absolute URLs for the Pages site", async ({ page }) => {
  await page.goto("");
  const pages = "https://cryptoguy1.github.io/pump-copilot/";
  await expect(page.locator('meta[property="og:image"]')).toHaveAttribute("content", `${pages}og.png`);
  await expect(page.locator('meta[property="og:url"]')).toHaveAttribute("content", pages);
  await expect(page.locator('meta[name="twitter:image"]')).toHaveAttribute("content", `${pages}og.png`);
});

// The README's screenshots (copied to docs/images/): the snapshot as the live demo shows it.
test("screenshots for the README", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1100 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  const real = manifest.sessions.find((s: { synthetic: boolean }) => !s.synthetic);
  const checked = answers.answers.find((a: { response: { served: string } }) =>
    a.response.served === "assistant");
  const shots: [string, string, (() => Promise<void>)?][] = [
    ["readme-fleet", ""],
    ["readme-asset-day", `assets/${real.asset_id}/${real.source_day}?session=${real.session_id}`],
    ["readme-case-assistant", `cases/${checked.case_id}`, async () => {
      await page.getByRole("button", { name: checked.question, exact: true }).click();
      await expect(page.getByTestId("recorded-label")).toBeVisible();
    }],
    ["readme-evaluation", "evaluation"],
  ];
  for (const [name, route, then] of shots) {
    await page.goto(route);
    await ready(page, 0);
    if (then) await then();
    await page.waitForTimeout(300);
    await page.screenshot({ path: resolve(OUT, `${name}-industrial-desktop.png`) });
  }
});
