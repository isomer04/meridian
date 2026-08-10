import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import type { AxeResults } from "axe-core";

test.setTimeout(120_000);

function seriousOrCritical(results: AxeResults) {
  return results.violations
    .filter((violation) => ["serious", "critical"].includes(violation.impact ?? ""))
    .map((violation) => violation.id);
}

function seriousOrCriticalDetails(results: AxeResults) {
  return results.violations
    .filter((violation) => ["serious", "critical"].includes(violation.impact ?? ""))
    .map(({ id, help, nodes }) => ({ id, help, nodes: nodes.map(({ target, html }) => ({ target, html })) }));
}

// Every top-level route, scanned at rest, @axe. `/loans/new` is already covered by
// `shell.spec.ts`'s setup scan, so it is not repeated here.
for (const [path, heading] of [
  ["/approvals", "Human gate workbench"],
  ["/evaluation", "Evaluation evidence"],
  ["/architecture", "Control hierarchy"],
] as const) {
  test(`${path} has no serious accessibility violations @axe`, async ({ page }) => {
    await page.goto(path);
    await expect(page.getByRole("heading", { name: heading })).toBeVisible({ timeout: 15_000 });
    const results = await new AxeBuilder({ page }).analyze();
    expect(seriousOrCritical(results)).toEqual([]);
  });
}

test("empty approval queue state has no serious accessibility violations @axe", async ({ page }) => {
  await page.goto("/approvals");
  await expect(page.getByText(/No approvals are waiting/)).toBeVisible();
  const results = await new AxeBuilder({ page }).analyze();
  expect(seriousOrCritical(results)).toEqual([]);
});

test("a running case (live activity) has no serious accessibility violations @axe", async ({
  page,
}) => {
  await page.goto("/loans/new");
  await page.getByRole("button", { name: "Use demo scenario" }).click();
  const select = page.locator("#scenario");
  await expect(select.locator('option[value="1"]')).toHaveCount(1);
  await select.selectOption("1");
  await page.getByRole("button", { name: "Start case run" }).click();
  await expect(
    page.getByLabel("Live activity").getByText(/Running case; live activity is updating/),
  ).toBeVisible({ timeout: 30_000 });
  const results = await new AxeBuilder({ page }).analyze();
  expect(seriousOrCritical(results)).toEqual([]);
  // Only one run is active per process — let this one finish before the next test
  // starts a new scenario, or that start would 409 against this still-running case.
  await expect(page.getByRole("heading", { name: "APPROVED" })).toBeVisible({ timeout: 60_000 });
});

// The awaiting-approval state is scanned from within `approvals.spec.ts` instead of
// here: that spec already owns pausing and deciding gates G1 (MER-1003) and G2
// (MER-1002), and a decided gate does not pause again on rerun. A second spec pausing
// the same loan/gate pair — or a third, unused one — would race or collide with that
// ownership rather than genuinely testing an independent state.

test("a completed determination has no serious accessibility violations @axe", async ({
  page,
}) => {
  await page.goto("/loans/new");
  await page.getByRole("button", { name: "Use demo scenario" }).click();
  const select = page.locator("#scenario");
  await expect(select.locator('option[value="1"]')).toHaveCount(1);
  await select.selectOption("1");
  await page.getByRole("button", { name: "Start case run" }).click();
  await expect(page.getByRole("heading", { name: "APPROVED" })).toBeVisible({ timeout: 60_000 });
  const results = await new AxeBuilder({ page }).analyze();
  expect(seriousOrCritical(results), JSON.stringify(seriousOrCriticalDetails(results), null, 2)).toEqual([]);
});

test("the API-unavailable shell state has no serious accessibility violations @axe", async ({
  page,
}) => {
  // Route every /api/v1/* request to a connection failure so the shell renders its
  // "Python API unavailable" runtime notice without needing the backend to be down.
  await page.route("**/api/v1/**", (route) => route.abort("failed"));
  await page.goto("/loans/new");
  await expect(page.getByText(/Python API unavailable/).first()).toBeVisible({ timeout: 15_000 });
  const results = await new AxeBuilder({ page }).analyze();
  expect(seriousOrCritical(results)).toEqual([]);
});
