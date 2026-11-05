import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  // Relative asset URLs, resolved against <base>, so the studio works under a prefix.
  base: "./",
  plugins: [react()],
  build: {
    outDir: "../src/munki_web_studio/static",
    emptyOutDir: true,
  },
  server: {
    proxy: { "/api": "http://127.0.0.1:3186", "/mcp": "http://127.0.0.1:3186" },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test-setup.ts"],
    include: ["src/**/*.test.tsx", "src/**/*.test.ts"],
  },
});
