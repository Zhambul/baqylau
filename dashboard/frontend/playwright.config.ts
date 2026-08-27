import { defineConfig, devices } from '@playwright/test';

const baseURL = process.env.BAQYLAU_E2E_BASE_URL ?? 'http://127.0.0.1:8794';
const workers = Number.parseInt(process.env.BAQYLAU_E2E_WORKERS ?? '16', 10);

export default defineConfig({
  expect: {
    timeout: 15_000,
  },
  forbidOnly: Boolean(process.env.CI),
  fullyParallel: true,
  reporter: process.env.CI ? 'github' : 'list',
  retries: 0,
  testDir: './tests',
  workers,
  use: {
    baseURL,
    screenshot: 'only-on-failure',
    serviceWorkers: 'block',
    trace: 'on-first-retry',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
    {
      name: 'webkit',
      use: { ...devices['Desktop Safari'] },
    },
  ],
});
