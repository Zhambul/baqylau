import { defineConfig, devices } from '@playwright/test';

const baseURL = process.env.BAQYLAU_E2E_BASE_URL ?? 'http://127.0.0.1:8794';
const managedServer = process.env.BAQYLAU_E2E_BASE_URL === undefined;
const python = process.env.BAQYLAU_E2E_PYTHON ?? 'python3';

function shellArgument(value: string): string {
  return `'${value.replaceAll("'", "'\\''")}'`;
}

export default defineConfig({
  expect: {
    timeout: 5_000,
  },
  forbidOnly: Boolean(process.env.CI),
  fullyParallel: false,
  reporter: process.env.CI ? 'github' : 'list',
  retries: process.env.CI ? 1 : 0,
  testDir: './tests',
  ...(managedServer
    ? {
        webServer: {
          command: `${shellArgument(python)} ../../tests/frontend_fixture_server.py`,
          reuseExistingServer: false,
          timeout: 30_000,
          url: baseURL + '/api/health',
        },
      }
    : {}),
  use: {
    baseURL,
    screenshot: 'only-on-failure',
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
