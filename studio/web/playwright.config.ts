import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 120_000,
  // The canvas stage is a fixed 800x480 in a grid column between two side panes
  // (260px + 320px); the default 1280px viewport leaves less than 800px for it,
  // so the stage visually overlaps the Changes pane and steals its clicks. Give
  // the page enough width that the three panes actually sit side by side.
  use: { baseURL: "http://127.0.0.1:8699", viewport: { width: 1600, height: 900 } },
  webServer: {
    command: "python3 -m studio --no-browser --port 8699 "
      + "--projects .e2e-projects --build-root .e2e-build",
    cwd: "../..",
    url: "http://127.0.0.1:8699/api/health",
    reuseExistingServer: false,
    timeout: 120_000,
  },
});
