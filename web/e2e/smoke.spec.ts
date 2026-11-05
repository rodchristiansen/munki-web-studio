import { expect, test } from "@playwright/test";

test("browses the fixture repository", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("button", { name: /ExampleBrowser/ })).toBeVisible();
  await page.getByRole("searchbox", { name: "Filter packages" }).fill("editor");
  await page.getByRole("button", { name: /^.*ExampleEditor\s*2\.10$/ }).click();
  await expect(page.getByRole("heading", { level: 2, name: "ExampleEditor" })).toBeVisible();
  await expect(page).toHaveURL(/q=editor/);
  await expect(page).toHaveURL(/path=pkgsinfo%2Fapps%2Fcreative%2FExampleEditor-2\.10\.plist/);
  await page.getByRole("button", { name: /ExampleEditorPlugin/ }).click();
  await expect(page.getByRole("heading", { level: 2, name: "ExampleEditorPlugin" })).toBeVisible();
  await page.goBack();
  await expect(page.getByRole("heading", { level: 2, name: "ExampleEditor" })).toBeVisible();
});

test("shows invalid files with their diagnostics", async ({ page }) => {
  await page.goto("/?path=pkgsinfo%2Finvalid%2FBroken-1.0.plist");
  await expect(page.getByRole("alert")).toContainText("could not be read");
  await expect(page.getByRole("alert")).toContainText("Line");
});

test("a deep link opens one version of a package", async ({ page }) => {
  await page.goto("/?path=pkgsinfo%2Fapps%2Fbrowsers%2FExampleBrowser-135.0.plist");
  await expect(page.getByRole("combobox", { name: "Version" })).toHaveValue("pkgsinfo/apps/browsers/ExampleBrowser-135.0.plist");
});

test("reads manifests, follows includes, and shows conditions as rules", async ({ page }) => {
  await page.goto("/?area=manifests&manifest=manifests%2Fgroups%2Fdesign-lab");
  await expect(page.getByRole("heading", { level: 2, name: "groups/design-lab" })).toBeVisible();
  await expect(page.getByText('machine type is "laptop" and macOS major version is at least 14')).toBeVisible();
  await expect(page.getByText("No such package")).toBeVisible();
  await page.getByRole("article").getByRole("button", { name: "site_default", exact: true }).click();
  await expect(page.getByRole("heading", { level: 2, name: "site_default" })).toBeVisible();
  await expect(page).toHaveURL(/manifest=manifests%2Fsite_default/);
  await page.goBack();
  await expect(page.getByRole("heading", { level: 2, name: "groups/design-lab" })).toBeVisible();
  await page.getByRole("button", { name: "ExampleEditor", exact: true }).click();
  await expect(page.getByRole("heading", { level: 2, name: "ExampleEditor" })).toBeVisible();
});
