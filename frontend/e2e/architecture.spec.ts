import { expect, test } from "@playwright/test";

test.setTimeout(60_000);

test("hierarchy diagram distinguishes the underwriter from the orchestrator", async ({
  page,
}) => {
  await page.goto("/architecture");
  await expect(page.getByRole("heading", { name: "Control hierarchy" })).toBeVisible();
  await expect(page.getByText("OriginationFlow", { exact: true })).toBeVisible();
  await expect(page.getByText(/Three peer LLM agents/)).toBeVisible();
  await expect(
    page.getByText(
      /Underwriter Agent makes the core loan judgment, but it does not lead/,
    ).first(),
  ).toBeVisible();
});

test("architecture.md and assumptions.md render with expected sections", async ({
  page,
}) => {
  await page.goto("/architecture");
  const main = page.getByRole("main");
  await expect(
    main.getByRole("heading", { name: "The thesis" }),
  ).toBeVisible({ timeout: 15_000 });
  await expect(main.getByText(/own judgment/).first()).toBeVisible();
  await expect(main.getByText(/not built/i).first()).toBeVisible();
  await expect(main.getByText(/actually breaks/i).first()).toBeVisible();
  await expect(
    main.getByRole("heading", { name: "What is measured vs. modeled" }),
  ).toBeVisible();
});

test("desktop contents navigation links to each document section", async ({ page }) => {
  await page.goto("/architecture");
  const nav = page.getByRole("navigation", { name: "Document contents" });
  await expect(nav).toBeVisible();
  await nav.getByRole("link", { name: "Assumptions" }).click();
  await expect(page).toHaveURL(/#assumptions-doc$/);
});

test("path traversal on the document endpoint cannot read arbitrary files", async ({
  request,
}) => {
  const traversal = await request.get(
    "/api/v1/documents/..%2f..%2fpyproject",
  );
  expect(traversal.status()).toBe(404);

  const unknownSlug = await request.get("/api/v1/documents/unknown");
  expect(unknownSlug.status()).toBe(422);
});

test("raw HTML embedded in a document is not executed by the renderer", async ({
  page,
}) => {
  // The rendered documents never include a live <script>/<img onerror> element even
  // though the architecture overview panel is otherwise raw markup-shaped text.
  await page.goto("/architecture");
  const document = page.locator("main .markdown-document").first();
  await expect(document).toBeVisible({ timeout: 15_000 });
  const scriptCount = await document.locator("script").count();
  expect(scriptCount).toBe(0);
  await expect(document.locator("img[onerror]")).toHaveCount(0);
});
