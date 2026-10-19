import { defineConfig } from "@playwright/test";
const reportRoot = process.env.TEST_REPORT_DIR || "runtime/browser-report";
const fixtureURL = `http://127.0.0.1:${process.env.TEST_PORT || 18787}`;

export default defineConfig({
  testDir: "./tests/browser",
  testMatch: "**/*.spec.mjs",
  fullyParallel: false,
  workers: 1,
  timeout: 30000,
  expect: { timeout: 8000 },
  retries: 0,
  use: {
    baseURL: fixtureURL,
    browserName: "chromium",
    viewport: { width: 1440, height: 1000 },
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    launchOptions: { args: ["--disable-dev-shm-usage"] },
  },
  reporter: [["list"], ["json", { outputFile: `${reportRoot}/results.json` }]],
  outputDir: `${reportRoot}/artifacts`,
  webServer: {
    command: "node tests/browser/server.mjs",
    url: `${fixtureURL}/__test/calls`,
    reuseExistingServer: false,
    timeout: 15000,
  },
});
