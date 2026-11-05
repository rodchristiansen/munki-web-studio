import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { expect, test } from "@playwright/test";

// The server edits a copy of the fixture repository (see playwright.config.ts).
const repo = fileURLToPath(new URL("../test-results/repo/", import.meta.url));

// One signed-in person has one open draft, so these run in order.
test.describe.configure({ mode: "serial" });

test("edits a field, reviews the diff, and saves it to the file", async ({
  page,
}) => {
  const path = "pkgsinfo/tools/ExampleCLI-3.0.yaml";
  const before = readFileSync(repo + path, "utf8");
  await page.goto(`/?path=${encodeURIComponent(path)}`);
  const field = page.getByRole("textbox", { name: "Display name" });
  await expect(field).toHaveValue("Example CLI");
  await field.fill("Example Command Line");
  await field.press("Tab");
  await page.getByRole("button", { name: /Review draft · 1 file/ }).click();
  const dialog = page.getByRole("dialog", { name: "1 file changed" });
  await expect(
    dialog.getByText("+display_name: Example Command Line"),
  ).toBeVisible();
  await dialog.getByRole("button", { name: "Save to repository" }).click();
  await expect(page.getByText("Saved 1 file to the repository.")).toBeVisible();
  const after = readFileSync(repo + path, "utf8");
  expect(after).toBe(
    before.replace(
      "display_name: Example CLI",
      "display_name: Example Command Line",
    ),
  );
});

test("a file changed on disk shows as a conflict until the edits are reapplied", async ({
  page,
}) => {
  const path = "pkgsinfo/apps/browsers/ExampleBrowser-135.0.plist";
  await page.goto(`/?path=${encodeURIComponent(path)}`);
  const field = page.getByRole("textbox", { name: "Display name" });
  await expect(field).toHaveValue("Example Browser");
  await field.fill("Example Browser ESR");
  await field.press("Tab");
  await expect(
    page.getByRole("button", { name: /Review draft · 1 file/ }),
  ).toBeVisible();

  const original = readFileSync(repo + path, "utf8");
  writeFileSync(
    repo + path,
    original.replace(
      "<string>Example Browser</string>",
      "<string>Example Browser (site)</string>",
    ),
  );

  await page.getByRole("button", { name: /Review draft/ }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("button", { name: "Save to repository" }).click();
  await expect(dialog.getByText("Changed on disk", { exact: true })).toBeVisible();
  await expect(
    dialog.getByRole("button", { name: "Save to repository" }),
  ).toBeDisabled();
  await dialog.getByRole("button", { name: "Reapply to current file" }).click();
  await expect(dialog.getByText("Changed on disk", { exact: true })).toHaveCount(0);
  await dialog.getByRole("button", { name: "Save to repository" }).click();
  await expect(page.getByText("Saved 1 file to the repository.")).toBeVisible();
  expect(readFileSync(repo + path, "utf8")).toContain(
    "<string>Example Browser ESR</string>",
  );
});

test("reorders and adds catalogs from the keyboard and saves the smallest change", async ({
  page,
}) => {
  const path = "manifests/computers/EXAMPLE-0001";
  const before = readFileSync(repo + path, "utf8");
  await page.goto(`/?area=manifests&manifest=${encodeURIComponent(path)}`);
  const catalogs = page.getByRole("region", { name: "Catalogs" });
  await catalogs.getByRole("button", { name: "Move production up" }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("button", { name: /Review draft · 1 file/ })).toBeVisible();
  await catalogs
    .getByRole("textbox", { name: "Add to Catalogs" })
    .fill("staging");
  await catalogs
    .getByRole("textbox", { name: "Add to Catalogs" })
    .press("Enter");
  await expect(catalogs.getByText("staging")).toBeVisible();
  await page.getByRole("button", { name: /Review draft · 1 file/ }).click();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Save to repository" })
    .click();
  await expect(page.getByText("Saved 1 file to the repository.")).toBeVisible();
  const after = readFileSync(repo + path, "utf8");
  expect(after).toBe(
    before.replace(
      "\t\t<string>testing</string>\n\t\t<string>production</string>\n",
      "\t\t<string>production</string>\n\t\t<string>testing</string>\n\t\t<string>staging</string>\n",
    ),
  );
});
