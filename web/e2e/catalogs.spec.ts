import { expect, test } from "@playwright/test";

test("shows catalog membership and explains what generating needs", async ({ page }) => {
  await page.goto("/?area=catalogs");
  await expect(page.getByRole("status").filter({ hasText: "Catalogs match the pkginfo" })).toBeVisible();
  const list = page.getByRole("complementary", { name: "Catalog list" });
  await list.getByRole("button", { name: /^.*production/ }).click();
  await expect(page).toHaveURL(/catalog=production/);
  await expect(page.getByRole("heading", { level: 2, name: "production" })).toBeVisible();
  await expect(page.getByRole("heading", { name: /Members \(\d+\)/ })).toBeVisible();
  await expect(list.getByRole("button", { name: "Generate catalogs" })).toBeDisabled();
  await expect(list.getByText(/Needs a macOS worker with makecatalogs/)).toBeVisible();
  await page.getByRole("article").getByRole("button", { name: "ExampleEditor", exact: true }).click();
  await expect(page.getByRole("heading", { level: 2, name: "ExampleEditor" })).toBeVisible();
});
