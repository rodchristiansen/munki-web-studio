import { defineConfig, devices } from "@playwright/test";

const port = 3197;

export default defineConfig({
  testDir: "e2e",
  use: { baseURL: `http://127.0.0.1:${port}` },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: `uv run mwsctl serve --port ${port} --config tests/fixtures/e2e.toml`,
    cwd: "..",
    url: `http://127.0.0.1:${port}/api/health`,
    reuseExistingServer: false,
  },
});
