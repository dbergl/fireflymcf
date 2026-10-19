import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://127.0.0.1:8676" } },
  build: { outDir: "dist" },
  test: {
    environment: "jsdom", globals: true, setupFiles: "./src/setupTests.ts",
    // e2e/ is Playwright's suite, not vitest's -- it imports @playwright/test's
    // own `test`, which throws when collected outside `playwright test`.
    exclude: ["**/node_modules/**", "**/e2e/**"],
  },
});
