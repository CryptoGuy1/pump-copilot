import { expect, test } from "@playwright/test";

// The whole loop on real CIRA data with a synthetic fault: start B_stuck_pressure at 60x, see
// the synthetic case appear live (no reload), acknowledge it, set a disposition with a reason,
// close it and check the export.
test("synthetic stuck-pressure case, live, from replay to export", async ({ page, request }) => {
  await page.goto("/replay");
  await expect(page.getByTestId("a8-notice")).toBeVisible();
  await page.getByLabel("asset-day").selectOption("cira-pump-B|2024-10-30");
  await page.getByLabel("speed", { exact: true }).selectOption("60");
  await page.getByLabel("scenario").selectOption("B_stuck_pressure");
  await page.getByRole("button", { name: "Start replay" }).click();
  const heading = page.getByRole("heading", { name: /Baseline progress, session #\d+/ });
  await expect(heading).toBeVisible();
  const sessionId = Number((await heading.textContent())!.match(/#(\d+)/)![1]);
  const row = page.getByTestId(`session-${sessionId}`);
  await expect(row.getByTestId("synthetic-label")).toBeVisible();
  await expect(page.getByTestId(`session-status-${sessionId}`)).toHaveText("running",
                                                                         { timeout: 30_000 });

  // the case list is not reloaded: the case arrives through /api/stream
  await page.goto(`/cases?session_id=${sessionId}`);
  await expect(page.getByTestId("live-status")).toContainText("open");
  const firstCase = page.locator("[data-testid^=case-row-]").first();
  await expect(firstCase).toBeVisible({ timeout: 240_000 });
  await expect(firstCase.getByTestId("synthetic-label")).toBeVisible();
  const caseId = Number((await firstCase.getAttribute("data-testid"))!.replace("case-row-", ""));

  await firstCase.getByRole("link", { name: `#${caseId}` }).click();
  await expect(page.getByRole("heading", { name: new RegExp(`Case #${caseId}`) })
    .getByTestId("synthetic-label")).toBeVisible();
  await expect(page.getByTestId("a8-notice")).toBeVisible();
  await expect(page.getByTestId("chart").first()).toBeVisible();
  const status = page.getByTestId("case-status");
  await expect(status).toHaveText("open");

  await page.getByRole("button", { name: "Acknowledge" }).click();
  await expect(status).toHaveText("acknowledged");
  await expect(page.getByRole("alert")).toHaveCount(0);

  // the assistant: no key here, so the evidence summary, labelled as such
  await page.getByLabel("question").fill("What does the evidence show? Should I stop the pump?");
  await page.getByRole("button", { name: "Ask" }).click();
  await expect(page.getByTestId("assistant-badge")).toHaveText("Evidence summary");
  await expect(page.getByTestId("assistant-fallback")).toContainText("no ANTHROPIC_API_KEY");
  const answer = page.getByTestId("assistant-answer"); // the answer, not the question box
  await expect(answer).toContainText("SYNTHETIC");
  await expect(answer).not.toContainText(/stop the pump|safe to operate/i);
  await page.getByTestId("ref-E2").first().click();
  await expect(page.locator("[data-highlighted=true]").first()).toBeVisible();
  await expect(page.getByTestId("highlight-note")).toContainText("E2");
  await page.getByRole("button", { name: "Approve and save" }).click();
  await expect(page.getByTestId("draft-saved")).toBeVisible();
  await expect(page.locator("ul li").filter({ hasText: /note by operator: SYNTHETIC scenario/ }))
    .toHaveCount(1);
  const reason = "e2e: outlet pressure held constant, stuck sensor (synthetic)";
  await page.getByLabel("disposition").selectOption(
    "escalate to reliability engineer (export only)");
  await page.getByPlaceholder("reason (required)").fill(reason);
  await page.getByRole("button", { name: "Set disposition" }).click();
  await expect(status).toHaveText("dispositioned");
  await expect(page.getByRole("alert")).toHaveCount(0);
  await page.getByRole("button", { name: "Close case" }).click();
  await expect(status).toHaveText("closed");
  await expect(page.getByRole("alert")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Acknowledge" })).toBeDisabled();

  await page.getByRole("button", { name: "Preview evidence pack" }).click();
  const pack = page.getByTestId("export-preview");
  await expect(pack).toContainText(`# Evidence pack: case ${caseId}`);
  await expect(pack).toContainText("SYNTHETIC");
  await expect(pack).toContainText(reason);
  await expect(pack).toContainText("sends nothing");
  const json = await (await request.get(`/api/cases/${caseId}/export`)).json();
  expect(json.synthetic).toBe(true);
  expect(json.case.status).toBe("closed");
  expect(json.case.disposition).toBe("escalate to reliability engineer (export only)");
  expect(json.assumptions).toContain("A8");
  expect(json.events.map((e: { event_type: string }) => e.event_type))
    .toEqual(expect.arrayContaining(["opened", "acknowledged", "disposition", "closed"]));
});
