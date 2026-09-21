import { defineConfig, devices } from "@playwright/test";
import Module from "node:module";
import { resolve } from "node:path";

// E2E specs live at the repository root while their dependencies remain local to the web app.
process.env.NODE_PATH = resolve(__dirname, "node_modules");
(Module as typeof Module & { _initPaths(): void })._initPaths();

const port = Number(process.env.PLAYWRIGHT_WEB_PORT || 3000);
const baseURL = `http://127.0.0.1:${port}`;

export default defineConfig({
  testDir: "../../tests/web",
  timeout: 30_000,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  use: {
    baseURL,
    trace: "retain-on-failure",
  },
  projects: [
    { name: "desktop-chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 720 } } },
  ],
});
