import { defineConfig, devices } from "@playwright/test";

const connectTimeoutSeconds = Number.parseInt(process.env.PLAYWRIGHT_CONNECT_TIMEOUT_SECONDS || "900", 10);
const connectTimeoutMs = Math.max(120, Number.isFinite(connectTimeoutSeconds) ? connectTimeoutSeconds : 900) * 1000;

export default defineConfig({
  testDir: "./e2e",
  timeout: connectTimeoutMs,
  expect: {
    timeout: 30_000,
  },
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: [["list"], ["html", { outputFolder: "playwright-report-rdp", open: "never" }]],
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL || "https://127.0.0.1:30073",
    ignoreHTTPSErrors: true,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
