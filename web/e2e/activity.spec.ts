import { expect, test } from "@playwright/test";

test("runs a validation job and keeps it across a reload", async ({ page }) => {
  await page.goto("/?area=activity");
  await page.getByRole("complementary", { name: "Jobs" }).getByRole("button", { name: "Validate the repository" }).click();
  await expect(page).toHaveURL(/job=/);
  const detail = page.getByRole("article");
  await expect(detail.getByText(/^Finished · started by/)).toBeVisible({ timeout: 15_000 });
  await expect(detail.getByText("errors", { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("article").getByText(/^Finished · started by/)).toBeVisible();
});
