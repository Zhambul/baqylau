import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { expect, test } from '@playwright/test';

import { digest, readAssets } from './assets.js';
import { buildPackages } from './builds.js';
import { serve } from './server.js';

test('loads a changed external module without rebuilding the host', async ({
  page,
}) => {
  const root = await mkdtemp(join(tmpdir(), 'baqylau-view-case-'));
  const built = await buildPackages(root);
  const server = await serve(built.assets);
  const errors: Error[] = [];
  page.on('pageerror', (error) => {
    errors.push(error);
  });
  try {
    await page.goto(
      `${server.url}/?first=${built.first}&second=${built.second}`,
    );
    await page.getByRole('button', { name: 'Load first', exact: true }).click();
    await expect(
      page.getByRole('heading', { name: 'Extension first' }),
    ).toBeVisible();
    await expect(page.locator('#host-text')).toHaveCSS(
      'color',
      'rgb(0, 80, 120)',
    );
    await expect(page.getByTestId('settings')).toHaveCSS(
      'color',
      'rgb(180, 90, 210)',
    );
    await page.getByRole('button', { name: 'Files', exact: true }).click();
    await expect(page.getByText('main.py', { exact: true })).toBeVisible();
    await page.getByRole('button', { name: 'Threads', exact: true }).click();
    await expect(
      page.getByText('Reply: I will check the error.'),
    ).toBeVisible();
    await page.getByRole('button', { name: 'Diff', exact: true }).click();
    await expect(page.getByLabel('Diff', { exact: true })).toContainText(
      '+ new line',
    );
    await page
      .getByRole('button', { name: 'Update settings', exact: true })
      .click();
    await expect(page.getByTestId('settings')).toHaveText(
      'Settings revision: 1',
    );
    await page.getByRole('button', { name: 'Send pulse', exact: true }).click();
    await expect(page.getByTestId('pulses')).toHaveText('Pulses: 1');
    const previous = await page.getByTestId('pulses').elementHandle();
    await page.getByRole('button', { name: 'Disable', exact: true }).click();
    await expect(page.locator('#view')).toBeEmpty();
    await page.getByRole('button', { name: 'Send pulse', exact: true }).click();
    expect(await previous.evaluate((element) => element.textContent)).toBe(
      'Pulses: 1',
    );
    await page
      .getByRole('button', { name: 'Load second', exact: true })
      .click();
    await expect(
      page.getByRole('heading', { name: 'Extension second' }),
    ).toBeVisible();
    await expect(page.getByTestId('pulses')).toHaveText('Pulses: 0');
    await page.getByRole('button', { name: 'Send pulse', exact: true }).click();
    await expect(page.getByTestId('pulses')).toHaveText('Pulses: 1');
    await page.getByRole('button', { name: 'Disable', exact: true }).click();
    await expect(page.locator('#view')).toBeEmpty();
    expect(built.second).not.toBe(built.first);
    expect(digest(await readAssets(built.hostDirectory))).toBe(
      built.hostDigest,
    );
    expect(errors).toEqual([]);
    await expect(page.locator('#failure')).toBeEmpty();
  } finally {
    await server.close();
    await rm(root, { recursive: true, force: true });
  }
});
