import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

test.setTimeout(90_000);

test("empty queue explains what will appear", async ({ page }) => {
  await page.goto("/approvals");
  await expect(page.getByRole("heading", { name: "Human gate workbench" })).toBeVisible();
  await expect(page.getByText(/No approvals are waiting/)).toBeVisible();
});

test("pause at G1, decide by name, then resume the loan from the durable ledger", async ({ page }) => {
  // Scenario 3 (low appraisal) pauses at gate G1 without auto-approve.
  await page.goto("/loans/new");
  await page.getByRole("button", { name: "Use demo scenario" }).click();
  const select = page.locator("#scenario");
  await expect(select.locator('option[value="3"]')).toHaveCount(1);
  await select.selectOption("3");
  await expect(select).toHaveValue("3");
  await page.getByRole("button", { name: "Start case run" }).click();

  await expect(page.getByRole("heading", { name: "Awaiting gate G1" })).toBeVisible({ timeout: 60_000 });
  const results = await new AxeBuilder({ page }).analyze();
  expect(
    results.violations.filter((v) => ["serious", "critical"].includes(v.impact ?? "")).map((v) => v.id),
  ).toEqual([]);
  const reviewLink = page.getByRole("link", { name: "Review gate G1" });
  await expect(reviewLink).toBeVisible();
  const href = await reviewLink.getAttribute("href");
  expect(href).toContain("loan_id=MER-1003");
  expect(href).toContain("gate=G1");

  await reviewLink.click();
  await expect(page.getByRole("heading", { name: "Human gate workbench" })).toBeVisible();
  await expect(page.getByText("GATE G1 — Denial")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Record a gate decision" })).toBeVisible();

  await page.getByLabel("Named approver").fill("Playwright Reviewer");
  await page.getByRole("button", { name: "Record gate decision" }).click();
  await expect(page.getByText(/Decision recorded for MER-1003 \/ G1/)).toBeVisible();
  await expect(page.getByText(/No approvals are waiting/)).toBeVisible();

  // Rerun the same scenario; the flow resumes from the durable ledger past the decided gate.
  await page.goto("/loans/new");
  await page.getByRole("button", { name: "Use demo scenario" }).click();
  await expect(select.locator('option[value="3"]')).toHaveCount(1);
  await select.selectOption("3");
  await expect(select).toHaveValue("3");
  await page.getByRole("button", { name: "Start case run" }).click();
  await expect(page.getByRole("heading", { name: "DENIED" })).toBeVisible({ timeout: 60_000 });
});

test("a stale digest is rejected and the queue must be refreshed", async ({ page, request }) => {
  // Uses scenario 2 (gate G2, loan MER-1002) rather than scenario 3/MER-1003 above —
  // once a gate is decided the loan's durable ledger resumes past it on any future run,
  // so reusing the same loan across tests would not reliably reproduce a pending gate.
  await page.goto("/loans/new");
  await page.getByRole("button", { name: "Use demo scenario" }).click();
  const select = page.locator("#scenario");
  await expect(select.locator('option[value="2"]')).toHaveCount(1);
  await select.selectOption("2");
  await expect(select).toHaveValue("2");
  await page.getByRole("button", { name: "Start case run" }).click();
  await expect(page.getByRole("heading", { name: "Awaiting gate G2" })).toBeVisible({ timeout: 60_000 });

  await page.goto("/approvals?loan_id=MER-1002&gate=G2");
  await expect(page.getByText("GATE G2 — Overlay exception")).toBeVisible();

  const queued = await request.get("/api/v1/approvals");
  const { approvals } = await queued.json();
  const item = approvals.find((entry: { loan_id: string; gate: string }) => entry.loan_id === "MER-1002" && entry.gate === "G2");
  expect(item).toBeTruthy();

  const decided = await request.post(`/api/v1/approvals/${item.loan_id}/${item.gate}/decision`, {
    data: { artifact_digest: item.artifact_digest, decision: "approved", approver: "Out Of Band Reviewer" },
  });
  expect(decided.status()).toBe(204);

  await page.getByLabel("Named approver").fill("Playwright Reviewer");
  await page.getByRole("button", { name: "Record gate decision" }).click();
  await expect(page.getByText(/This artifact changed after the queue loaded/)).toBeVisible();
});
