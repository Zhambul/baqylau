import { spawn, type ChildProcess } from 'node:child_process';
import { writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';

import { expect, test as base } from '@playwright/test';

type TestFixtures = {
  fixtureBaseURL: string;
};

const fixtureServer = fileURLToPath(
  new URL('../../../tests/frontend_fixture_server.py', import.meta.url),
);

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

async function stop(child: ChildProcess): Promise<void> {
  if (child.exitCode !== null) return;
  const exited = new Promise<void>((resolve) => {
    child.once('exit', () => {
      resolve();
    });
  });
  child.kill('SIGTERM');
  const stopped = await Promise.race([
    exited.then(() => true),
    delay(2_000).then(() => false),
  ]);
  if (!stopped) {
    child.kill('SIGKILL');
    await exited;
  }
}

export const test = base.extend<TestFixtures>({
  fixtureBaseURL: async ({ browserName }, use, testInfo) => {
    const external = process.env.BAQYLAU_E2E_BASE_URL;
    if (external !== undefined) {
      await use(external);
      return;
    }

    const python = process.env.BAQYLAU_E2E_PYTHON ?? 'python3';
    let output = `browser: ${browserName}\n`;
    const child = spawn(python, [fixtureServer], {
      env: { ...process.env, BAQYLAU_E2E_PORT: '0' },
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
      await stop(child);
      if (testInfo.status !== testInfo.expectedStatus && output.length > 0) {
        const logPath = testInfo.outputPath('fixture-server.log');
        await writeFile(logPath, output, 'utf8');
        await testInfo.attach('fixture-server.log', {
          path: logPath,
          contentType: 'text/plain',
        });
      }
    }
  },
  baseURL: async ({ fixtureBaseURL }, use) => {
    await use(fixtureBaseURL);
  },
});

export { expect };
