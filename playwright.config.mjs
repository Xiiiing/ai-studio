import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests/browser', testMatch: '**/*.spec.mjs', fullyParallel: false, workers: 1,
  timeout: 30000, expect: { timeout: 8000 }, retries: 0,
  use: { baseURL: 'http://127.0.0.1:18787', browserName: 'chromium', viewport: { width: 1440, height: 1000 },
    trace: 'retain-on-failure', screenshot: 'only-on-failure', launchOptions: { args: ['--disable-dev-shm-usage'] } },
  reporter: [['list'], ['json', { outputFile: 'runtime/browser-report/results.json' }]],
  outputDir: 'runtime/browser-report/artifacts',
  webServer: { command: 'node tests/browser/server.mjs', url: 'http://127.0.0.1:18787/__test/calls', reuseExistingServer: false, timeout: 15000 }
});
