import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

test("run setup provides labelled navigation and configuration", async ({ page }) => {
  await page.goto("/loans/new");
  await expect(page.getByRole("heading", { name: "New underwriting case" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Primary navigation" })).toBeVisible();
});

test("setup has no serious accessibility violations @axe", async ({ page }) => {
  await page.goto("/loans/new");
  const results = await new AxeBuilder({ page }).analyze();
  expect(
    results.violations
      .filter((violation) => ["serious", "critical"].includes(violation.impact ?? ""))
      .map((violation) => violation.id),
  ).toEqual([]);
});

test("mobile shell keeps its gutter, width, and disclosure state across navigation", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/loans/new");
  await expect(page.getByLabel("Select PDF documents")).toBeVisible();

  const horizontalOverflow = () =>
    page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
  expect(await horizontalOverflow()).toBe(0);

  const brand = page.getByRole("link", { name: "Meridian home" });
  await page.getByText("Navigation").click();
  expect(await brand.evaluate((element) => element.getBoundingClientRect().width)).toBe(40);
  expect(await horizontalOverflow()).toBe(0);

  await page.getByRole("link", { name: "Architecture" }).click();
  await expect(page.getByRole("heading", { name: "Control hierarchy" })).toBeVisible();
  await expect(page.locator(".mobile-nav details")).not.toHaveAttribute("open", "");
  expect(await horizontalOverflow()).toBe(0);
});
