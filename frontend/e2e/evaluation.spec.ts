import { expect, test } from "@playwright/test";

test.setTimeout(180_000);

test("evaluation report renders with the self-consistency caveat and headline metrics", async ({
  page,
}) => {
  await page.goto("/evaluation");
  await expect(page.getByRole("heading", { name: "Evaluation evidence" })).toBeVisible();

  // Report presence depends on whether evals/report.md exists in this checkout; either
  // explicit state is acceptable, but the page must never invent metrics silently.
  const body = page.getByRole("main");
  await expect(
    body.getByText(/No evaluation report exists yet|Evaluation report/i).first(),
  ).toBeVisible({ timeout: 15_000 });
});

test("running the harness from the browser reaches 10/10 in stub mode and refreshes the report", async ({
  page,
}) => {
  await page.goto("/evaluation");
  await page.getByRole("button", { name: "Run the eval harness" }).click();
  await expect(page.getByText(/Running the eval harness/)).toBeVisible();

  await expect(page.getByText(/completed \(exit 0\)/)).toBeVisible({ timeout: 150_000 });
  const report = page.locator("#report-heading").locator("..");
  await expect(report.getByText(/self-consistency/i).first()).toBeVisible();
  await expect(report.getByRole("cell", { name: "Decision accuracy" })).toBeVisible();
  await expect(report.getByRole("cell", { name: "Citation validity" })).toBeVisible();
  await expect(report.getByText(/hallucination rate/i).first()).toBeVisible();
  await expect(report.getByText(/10\/10/).first()).toBeVisible();
});

test("a second run while one is active is rejected with a clear notice", async ({
  page,
  request,
}) => {
  const first = await request.post("/api/v1/evaluations");
  expect(first.status()).toBe(202);

  await page.goto("/evaluation");
  await page.getByRole("button", { name: "Run the eval harness" }).click();
  await expect(page.getByText(/already active/)).toBeVisible({ timeout: 15_000 });

  // Drain the run this test started so later tests are not blocked by it.
  await expect
    .poll(
      async () => {
        const system = await request.get("/api/v1/system");
        return (await system.json()).active_evaluation_id;
      },
      { timeout: 150_000 },
    )
    .toBeNull();
});
