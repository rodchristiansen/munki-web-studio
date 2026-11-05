import { expect, test } from "@playwright/test";

test("Settings explains which tool a missing worker needs", async ({ page }) => {
  await page.goto("/?area=settings");
  await expect(page.getByText("No workers are configured", { exact: false })).toBeVisible();
  await expect(page.getByText("Needs a macOS worker with makecatalogs; none is connected")).toBeVisible();
  await page.getByRole("button", { name: "Activity" }).click();
  await expect(page.getByRole("button", { name: "Build catalogs with makecatalogs" })).toBeDisabled();
  await expect(page.getByText(/Build catalogs with makecatalogs: Needs a macOS worker with makecatalogs/)).toBeVisible();
});
