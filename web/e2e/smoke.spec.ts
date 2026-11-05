import { expect, test } from "@playwright/test";

test("the workspace shell renders from the FastAPI server", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("navigation", { name: "Work areas" })).toBeVisible();
  await page.getByRole("button", { name: /Inkscape/ }).click();
  await expect(page.getByRole("heading", { level: 2, name: "Inkscape" })).toBeVisible();
  await page.getByRole("tab", { name: "Overview" }).focus();
  await page.keyboard.press("ArrowRight");
  await expect(page.getByRole("tab", { name: "Detection" })).toHaveAttribute("aria-selected", "true");
});
