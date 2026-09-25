import { createHash } from 'node:crypto';
import { readFile, writeFile } from 'node:fs/promises';
import { join } from 'node:path';

import type { Page } from '@playwright/test';

import { expect, test } from './fixtures';

test.use({ fixtureModule: 'tests.extension_web.view_server' });

const OWNER = 'test.view';
// The sample package's module path (tests/extension_api/manifest_samples.py).
const MODULE_PATH = 'web/dist/extension.js';
const PAGE = `/#/w/workspace-one/x/${OWNER}/${OWNER}.main`;

async function change(
  page: Page,
  action: 'Enable' | 'Disable' | 'Reload',
): Promise<void> {
  await page.goto('/#/settings/extensions');
  const card = page
    .locator('article')
    .filter({ has: page.getByText(OWNER, { exact: true }) });
  await card.getByRole('button', { name: action, exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Review extension change' });
  await dialog.getByRole('button', { name: 'Apply change' }).click();
  await expect(card.locator('.state')).toHaveText(
    action === 'Disable' ? 'disabled' : 'enabled',
  );
}

/** Replace the package module on disk and declare its new digest. */
async function changeModule(
  packageRoot: string,
  source: string,
): Promise<void> {
  const directory = join(packageRoot, OWNER);
  const modulePath = join(directory, MODULE_PATH);
  const sha256 = (bytes: string | Buffer): string =>
    createHash('sha256').update(bytes).digest('hex');
  const previous = sha256(await readFile(modulePath));
  await writeFile(modulePath, source);
  const manifestPath = join(directory, 'extension.json');
  const manifest = await readFile(manifestPath, 'utf8');
  await writeFile(manifestPath, manifest.replace(previous, sha256(source)));
}

/** The host build files that the page loaded. */
async function hostAssets(page: Page): Promise<string[]> {
  return page.evaluate(() =>
    [
      ...document.querySelectorAll<HTMLScriptElement>('script[src]'),
      ...document.querySelectorAll<HTMLLinkElement>('link[rel="stylesheet"]'),
    ].map((element) =>
      element instanceof HTMLScriptElement ? element.src : element.href,
    ),
  );
}

test('mounts a separately built view on its workspace page', async ({
  page,
}) => {
  await change(page, 'Enable');
  await page.goto(PAGE);
  await page.reload();

  await expect(
    page.getByText(`Hello from ${OWNER} in workspace`),
  ).toBeVisible();
});

test('lists the recent operations of a package', async ({ page }) => {
  await change(page, 'Enable');
  const card = page
    .locator('article')
    .filter({ has: page.getByText(OWNER, { exact: true }) });
  await card.getByText('Recent operations').click();
  const latest = card.locator('.operations li').first();
  await expect(latest).toContainText('enable');
  await expect(latest).toContainText('succeeded');
});

test('keeps package styles inside the view', async ({ page }) => {
  await change(page, 'Enable');
  await page.goto(PAGE);

  const greeting = page.getByText(`Hello from ${OWNER} in workspace`);
  await expect(greeting).toHaveCSS('color', 'rgb(255, 0, 0)');
  const hostLink = page.getByRole('link', { name: 'Workspace settings' });
  await expect(hostLink).toBeVisible();
  const hostColor = await page
    .locator('p.page-links')
    .evaluate((element) => getComputedStyle(element).color);
  expect(hostColor).not.toBe('rgb(255, 0, 0)');
});

test('reloads a changed package without a new host build', async ({
  page,
  packageRoot,
}) => {
  await change(page, 'Enable');
  await page.goto(PAGE);
  await expect(
    page.getByText(`Hello from ${OWNER} in workspace`),
  ).toBeVisible();
  const before = await hostAssets(page);

  await changeModule(
    packageRoot,
    `export function mount(target) {
  const line = target.ownerDocument.createElement('p');
  line.textContent = 'Changed package view';
  target.append(line);
  return { update() {}, dispose() { line.remove(); } };
}
`,
  );
  await page.goto('/#/settings/extensions');
  await page.getByRole('button', { name: 'Rescan packages' }).click();
  const card = page
    .locator('article')
    .filter({ has: page.getByText(OWNER, { exact: true }) });
  const differs = card.getByText('The source differs from the active package');
  await expect(differs).toBeVisible();
  await change(page, 'Reload');
  await expect(differs).toHaveCount(0);
  await page.goto(PAGE);
  await page.reload();

  await expect(
    page.locator('#view').getByText('Changed package view'),
  ).toBeVisible();
  expect(await hostAssets(page)).toEqual(before);
});

test('mounts installation views in the toolbar and the status line', async ({
  page,
}) => {
  await change(page, 'Enable');
  await page.goto('/#/');
  await page.reload();

  const greeting = `Hello from ${OWNER} in installation`;
  await expect(
    page.locator('[data-slot="toolbar"]').getByText(greeting),
  ).toBeVisible();
  await expect(
    page.locator('[data-slot="status"]').getByText(greeting),
  ).toBeVisible();
});

test('shows the unavailable state after the package is disabled', async ({
  page,
}) => {
  await change(page, 'Enable');
  await change(page, 'Disable');
  await page.goto(PAGE);
  await page.reload();

  await expect(
    page.getByText('This extension view is not available.'),
  ).toBeVisible();
  await page.goto('/#/');
  await expect(page.locator('[data-slot="toolbar"]')).toHaveCount(0);
});
