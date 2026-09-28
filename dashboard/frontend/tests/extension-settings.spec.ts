import type { Page } from '@playwright/test';

import { expect, test } from './fixtures';

test.use({ fixtureModule: 'tests.extension_web.settings_server' });

const OWNER = 'test.settings';
const SETTINGS = `/#/settings/extensions/${OWNER}`;

async function enable(page: Page): Promise<void> {
  await page.goto('/#/settings/extensions');
  const card = page
    .locator('article')
    .filter({ has: page.getByText(OWNER, { exact: true }) });
  await card.getByRole('button', { name: 'Enable', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Review extension change' });
  await dialog.getByRole('button', { name: 'Apply change' }).click();
  await expect(card.locator('.state')).toHaveText('enabled');
}

test('saves, overrides a workspace, and resets through the standard form', async ({
  page,
}) => {
  await enable(page);
  await page.goto(SETTINGS);
  const label = page.getByLabel('Label');
  await expect(label).toHaveValue('Default');

  await label.fill('Everywhere');
  await page.getByRole('button', { name: 'Submit' }).click();
  await expect(
    page.getByText(/Recorded history does not change/),
  ).toBeVisible();
  await page.reload();
  await expect(page.getByLabel('Label')).toHaveValue('Everywhere');

  await page.goto(`${SETTINGS}/w/workspace-one`);
  await expect(page.getByLabel('Label')).toHaveValue('Everywhere');
  await expect(page.getByText(/uses the inherited values/)).toBeVisible();
  await page.getByLabel('Label').fill('Only here');
  await page.getByRole('button', { name: 'Submit' }).click();
  await expect(page.getByText(/has its own values/)).toBeVisible();

  await page.getByRole('button', { name: 'Reset to inherited values' }).click();
  await expect(page.getByText(/uses the inherited values/)).toBeVisible();
  await expect(page.getByLabel('Label')).toHaveValue('Everywhere');
});

test('refuses a stale save from a second client (C05)', async ({
  page,
  context,
}) => {
  await enable(page);
  const second = await context.newPage();
  await page.goto(SETTINGS);
  await second.goto(SETTINGS);
  await expect(second.getByLabel('Label')).toHaveValue('Default');

  await page.getByLabel('Label').fill('First client');
  await page.getByRole('button', { name: 'Submit' }).click();
  await expect(
    page.getByText(/Recorded history does not change/),
  ).toBeVisible();
  await second.getByLabel('Label').fill('Second client');
  await second.getByRole('button', { name: 'Submit' }).click();
  await expect(
    second.getByText('The settings changed after they were read.', {
      exact: false,
    }),
  ).toBeVisible();

  await second.reload();
  await expect(second.getByLabel('Label')).toHaveValue('First client');
});

test('refuses a value that the schema does not accept', async ({ page }) => {
  await enable(page);
  await page.goto(SETTINGS);
  await page.getByLabel('Label').fill('');
  await page.getByRole('button', { name: 'Submit' }).click();

  await expect(page.getByText(/Recorded history does not change/)).toHaveCount(
    0,
  );
  await page.reload();
  await expect(page.getByLabel('Label')).toHaveValue('Default');
});

test('a custom panel saves through the same settings API', async ({ page }) => {
  await enable(page);
  await page.goto(SETTINGS);
  await page.getByRole('button', { name: 'Save from panel' }).click();
  await expect(page.getByText('Panel saved')).toBeVisible();

  await page.reload();
  await expect(page.getByLabel('Label')).toHaveValue('From panel');
});

test('stores a secret without showing it again', async ({ page }) => {
  await enable(page);
  await page.goto(SETTINGS);
  const secrets = page.getByRole('region', { name: 'Secrets' });
  await expect(secrets.getByText('Not set')).toBeVisible();

  await secrets.getByLabel('token').fill('s3cret-value');
  await secrets.getByRole('button', { name: 'Save secret' }).click();
  await expect(secrets.getByText('Set', { exact: true })).toBeVisible();
  await expect(secrets.getByLabel('token')).toHaveValue('');

  const reply = await page.request.get(`/api/extensions/${OWNER}/secrets`);
  expect(await reply.text()).not.toContain('s3cret-value');
  await secrets.getByRole('button', { name: 'Clear' }).click();
  await expect(secrets.getByText('Not set')).toBeVisible();
});
