import { expect, test, type Page } from "@playwright/test";

test.setTimeout(90_000);

async function startScenario(page: Page, scenarioId: string, options: { autoApprove?: boolean; policyAttack?: boolean; submitTwice?: boolean } = {}) {
  await page.goto("/loans/new");
  await page.getByRole("button", { name: "Use demo scenario" }).click();
  const select = page.locator("#scenario");
  await expect(select).toBeVisible();
  // The scenario list loads asynchronously; selecting before it populates silently
  // keeps the placeholder's default (scenario 1). Wait for the target option to exist,
  // then confirm the selection actually stuck before proceeding.
  await expect(select.locator(`option[value="${scenarioId}"]`)).toHaveCount(1);
  await select.selectOption(scenarioId);
  await expect(select).toHaveValue(scenarioId);
  if (options.autoApprove || options.policyAttack || options.submitTwice) {
    await page.getByText("Demonstration controls").click();
    if (options.autoApprove) await page.getByLabel("Auto-approve human gates").check();
    if (options.policyAttack) await page.getByLabel("Inject a policy-gate attack").check();
    if (options.submitTwice) await page.getByLabel("Submit twice to inspect idempotency").check();
  }
  await page.getByRole("button", { name: "Start case run" }).click();
}

test("scenario 1 starts, streams activity, and renders its completed determination", async ({ page }) => {
  await startScenario(page, "1");
  await expect(page.getByText(/MER-1001 \/ SCENARIO 1/)).toBeVisible();
  await expect(page.getByLabel("Live activity").getByText("flow.step", { exact: false }).first()).toBeVisible({ timeout: 60_000 });
  await expect(page.getByRole("heading", { name: "APPROVED" })).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText(/Citation verification/)).toBeVisible();
  await expect(page.getByText(/Value acceptance: exercised/)).toBeVisible();
});

test("policy attack is held and the run still reaches its determination", async ({ page }) => {
  // The Reg Z gate raises PolicyViolation, which the flow catches and records as a held
  // control — it does not abort the run. Auto-approve is required here so the run reaches
  // a terminal state instead of pausing at scenario 2's normal human gate.
  await startScenario(page, "2", { policyAttack: true, autoApprove: true });
  await expect(page.getByRole("heading", { name: "APPROVED WITH CONDITIONS" })).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText(/policy control held/i)).toBeVisible();
  await expect(page.getByText(/1026\.19\(e\)\(2\)\(i\)\(A\)/).first()).toBeVisible();
});

test("scenario 2 preserves the overlay exception and specialist evidence", async ({ page }) => {
  await startScenario(page, "2", { autoApprove: true });
  await expect(page.getByRole("heading", { name: "APPROVED WITH CONDITIONS" })).toBeVisible({ timeout: 60_000 });
  await expect(page.getByRole("cell", { name: /OV-DTI-02/ }).first()).toBeVisible();
  await expect(page.getByText("Exception granted")).toBeVisible();
  await expect(page.getByText(/self_employed_specialist_agent/).first()).toBeVisible();
  await expect(page.getByText(/prior_to_docs/).first()).toBeVisible();
});

test("scenario 3 preserves denial and reverse compensation", async ({ page }) => {
  await startScenario(page, "3", { autoApprove: true });
  await expect(page.getByRole("heading", { name: "DENIED" })).toBeVisible({ timeout: 60_000 });
  await expect(page.getByRole("cell", { name: /OV-LTV-03/ }).first()).toBeVisible();
  await expect(page.getByText("Compensation unwound in reverse")).toBeVisible();
  await expect(page.getByText(/adverse_action/).first()).toBeVisible();
  await expect(page.getByText(/Cycle-time model/)).toBeVisible();
});

test("duplicate submission retains one tri-merge inquiry", async ({ page }) => {
  await startScenario(page, "4", { autoApprove: true, submitTwice: true });
  await expect(page.getByRole("heading", { name: "APPROVED" })).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText(/Idempotency evidence/)).toBeVisible();
  await expect(page.getByText(/Two full submissions made 1 tri-merge calls/)).toBeVisible();
});
