import { spawn, type ChildProcess } from 'node:child_process';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';

import { expect, test as base } from '@playwright/test';

type TestFixtures = {
  /** Where an extension fixture server keeps its packages; a test may change them. */
  packageRoot: string;
  fixtureBaseURL: string;
  fixtureModule: string;
  extensionReadOnly: boolean;
};

const repositoryRoot = fileURLToPath(new URL('../../../', import.meta.url));

/**
 * The fixture sessions' working directory: the checkout that runs the tests.
 * In a linked worktree, the page shows the main checkout as the project
 * directory, so a test that needs the session's own directory uses this.
 */
export const fixtureWorkingDirectory = repositoryRoot.replace(/\/$/, '');

async function waitUntilHealthy(
  child: ChildProcess,
  output: () => string,
): Promise<string> {
  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    if (child.exitCode !== null) {
      throw new Error(`fixture server exited early\n${output()}`);
    }
    const match = /BAQYLAU_FIXTURE_URL=(http:\/\/127\.0\.0\.1:\d+)/.exec(
      output(),
    );
    const baseURL = match?.[1];
    if (baseURL === undefined) {
      await delay(50);
      continue;
    }
    try {
      const response = await fetch(`${baseURL}/api/health`);
      if (response.ok) return baseURL;
    } catch {
      // The worker can connect after Python finishes fixture setup.
    }
    await delay(50);
  }
  throw new Error(`fixture server did not become healthy\n${output()}`);
}

async function stop(child: ChildProcess, timeoutMs: number): Promise<boolean> {
  if (child.exitCode !== null) return child.exitCode === 0;
  const exited = new Promise<{ stopped: boolean; clean: boolean }>(
    (resolve) => {
      child.once('exit', (code) => {
        resolve({ stopped: true, clean: code === 0 });
      });
    },
  );
  child.kill('SIGTERM');
  const stopped = await Promise.race([
    exited,
    delay(timeoutMs).then(() => ({ stopped: false, clean: false })),
  ]);
  if (!stopped.stopped) {
    child.kill('SIGKILL');
    await exited;
  }
  return stopped.clean;
}

export const test = base.extend<TestFixtures>({
  fixtureModule: ['tests.frontend_fixture_server', { option: true }],
  extensionReadOnly: [false, { option: true }],
  // eslint-disable-next-line no-empty-pattern -- Playwright requires the fixture argument.
  packageRoot: async ({}, use) => {
    const root = await mkdtemp(join(tmpdir(), 'baqylau-e2e-packages-'));
    try {
      await use(root);
    } finally {
      await rm(root, { recursive: true, force: true });
    }
  },
  fixtureBaseURL: async (
    { browserName, fixtureModule, extensionReadOnly, packageRoot },
    use,
    testInfo,
  ) => {
    const external = process.env.BAQYLAU_E2E_BASE_URL;
    if (external !== undefined) {
      if (fixtureModule !== 'tests.frontend_fixture_server')
        throw new Error(
          'Extension write tests require their own private daemon.',
        );
      await use(external);
      return;
    }

    const python = process.env.BAQYLAU_E2E_PYTHON ?? 'python3';
    let output = `browser: ${browserName}\n`;
    const child = spawn(python, ['-m', fixtureModule], {
      cwd: repositoryRoot,
      env: {
        ...process.env,
        BAQYLAU_E2E_PORT: '0',
        BAQYLAU_E2E_EXTENSION_READ_ONLY: extensionReadOnly ? '1' : '0',
        BAQYLAU_E2E_PACKAGES: packageRoot,
      },
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    child.stdout.on('data', (chunk: Buffer) => {
      output += chunk.toString();
    });
    child.stderr.on('data', (chunk: Buffer) => {
      output += chunk.toString();
    });

    try {
      const baseURL = await waitUntilHealthy(child, () => output);
      await use(baseURL);
    } finally {
      const managed = fixtureModule !== 'tests.frontend_fixture_server';
      const stopped = await stop(child, managed ? 15_000 : 2_000);
      if (
        (testInfo.status !== testInfo.expectedStatus ||
          (managed && !stopped)) &&
        output.length > 0
      ) {
        const logPath = testInfo.outputPath('fixture-server.log');
        await writeFile(logPath, output, 'utf8');
        await testInfo.attach('fixture-server.log', {
          path: logPath,
          contentType: 'text/plain',
        });
      }
      if (managed)
        expect
          .soft(stopped, 'The private extension daemon must stop cleanly.')
          .toBe(true);
    }
  },
  baseURL: async ({ fixtureBaseURL }, use) => {
    await use(fixtureBaseURL);
  },
});

export { expect };
