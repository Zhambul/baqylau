import AxeBuilder from '@axe-core/playwright';
import type { Locator, Page } from '@playwright/test';

import { expect, test } from './fixtures';

test.use({ fixtureModule: 'tests.extension_web.management_server' });

function card(page: Page, owner: string): Locator {
  return page
    .locator('article')
    .filter({ has: page.getByText(owner, { exact: true }) });
}

async function enable(page: Page, owner: string): Promise<void> {
  await card(page, owner)
    .getByRole('button', { name: 'Enable', exact: true })
    .click();
  const dialog = page.getByRole('dialog', { name: 'Review extension change' });
  await expect(dialog.getByRole('listitem')).toHaveText([owner]);
  await dialog.getByRole('button', { name: 'Apply change' }).click();
  await expect(dialog).not.toBeVisible();
  await expect(card(page, owner).locator('.state')).toHaveText('enabled');
  await expect(
    page.getByRole('button', { name: 'Refresh', exact: true }),
  ).toBeEnabled();
}

test('manages real packages and confirms transitive dependent removal', async ({
  page,
}) => {
  await page.goto('/#/settings/extensions');
  await expect(
    page.getByRole('heading', { name: 'Extensions', exact: true }),
  ).toBeVisible();
  await enable(page, 'test.base');
  await enable(page, 'test.child');
  await enable(page, 'test.leaf');
  await enable(page, 'test.optional');

  await card(page, 'test.base')
    .getByRole('button', { name: 'Disable' })
    .click();
  const dialog = page.getByRole('dialog', { name: 'Review extension change' });
  await expect(dialog.getByRole('listitem')).toHaveText([
    'test.leaf',
    'test.child',
    'test.base',
  ]);
  await dialog.getByRole('button', { name: 'Apply change' }).click();
  await expect(card(page, 'test.base').locator('.state')).toHaveText(
    'disabled',
  );
  await expect(card(page, 'test.child').locator('.state')).toHaveText(
    'disabled',
  );
  await expect(card(page, 'test.leaf').locator('.state')).toHaveText(
    'disabled',
  );
  await expect(card(page, 'test.optional').locator('.state')).toHaveText(
    'enabled',
  );

  await page.reload();
  await expect(card(page, 'test.optional').locator('.state')).toHaveText(
    'enabled',
  );
  await page.getByRole('button', { name: 'Rescan packages' }).click();
  await expect(
    page.getByRole('button', { name: 'Rescan packages' }),
  ).toBeEnabled();
});

test('rejects a stale confirmation from a second browser page', async ({
  page,
  context,
}) => {
  await page.goto('/#/settings/extensions');
  await card(page, 'test.base')
    .getByRole('button', { name: 'Enable', exact: true })
    .click();
  const second = await context.newPage();
  await second.goto('/#/settings/extensions');
  await enable(second, 'test.base');

  await page
    .getByRole('dialog')
    .getByRole('button', { name: 'Apply change' })
    .click();
  await expect(page.getByRole('dialog')).not.toBeVisible();
  await expect(page.getByRole('alert')).toContainText('Refresh and review');
  await expect(
    card(page, 'test.base').getByRole('button', {
      name: 'Enable',
      exact: true,
    }),
  ).toBeDisabled();
  await page.getByRole('button', { name: 'Refresh', exact: true }).click();
  await expect(card(page, 'test.base').locator('.state')).toHaveText('enabled');
  await second.close();
});

test('supports keyboard confirmation and accessible management structure', async ({
  page,
}) => {
  await page.goto('/');
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await expect(page).toHaveURL(/#\/settings\/extensions$/);
  const trigger = card(page, 'test.base').getByRole('button', {
    name: 'Enable',
    exact: true,
  });
  await trigger.focus();
  await page.keyboard.press('Enter');
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByRole('button', { name: 'Cancel' })).toBeFocused();
  const scan = await new AxeBuilder({ page }).analyze();
  expect(
    scan.violations.filter(
      (violation) =>
        violation.impact === 'critical' || violation.impact === 'serious',
    ),
  ).toEqual([]);
  await page.keyboard.press('Escape');
  await expect(dialog).not.toBeVisible();
  await expect(trigger).toBeFocused();
  const pageScan = await new AxeBuilder({ page })
    .include('.extension-settings')
    .analyze();
  expect(
    pageScan.violations.filter(
      (violation) =>
        violation.impact === 'critical' || violation.impact === 'serious',
    ),
  ).toEqual([]);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.getByRole('link', { name: 'baqylau', exact: true }).click();
  await expect(
    page.getByRole('heading', { name: 'Extensions', exact: true }),
  ).toHaveCount(0);
});

test('shows discovery and preparation failures without removing the active package', async ({
  page,
}) => {
  await page.goto('/#/settings/extensions');
  await expect(
    page.getByRole('article', { name: 'Invalid package', exact: true }),
  ).toContainText('invalid_manifest');
  await enable(page, 'test.base');
  await card(page, 'test.failed')
    .getByRole('button', { name: 'Enable', exact: true })
    .click();
  await page
    .getByRole('dialog')
    .getByRole('button', { name: 'Apply change' })
    .click();
  await expect(
    page.getByRole('region', { name: 'Last observed operation' }),
  ).toContainText('preparation_failed');
  await expect(card(page, 'test.failed').locator('.state')).toHaveText(
    'disabled',
  );
  await expect(card(page, 'test.base').locator('.state')).toHaveText('enabled');
});

test.describe('read-only extension management', () => {
  test.use({ extensionReadOnly: true });

  test('keeps reads available and rejects management writes', async ({
    page,
    request,
  }) => {
    await page.goto('/#/settings/extensions');
    await expect(
      page.getByText('Extension changes are read-only.', { exact: false }),
    ).toBeVisible();
    await expect(
      card(page, 'test.base').getByRole('button', {
        name: 'Enable',
        exact: true,
      }),
    ).toBeDisabled();
    await expect(
      page.getByRole('button', { name: 'Rescan packages' }),
    ).toBeDisabled();
    await expect(
      page.getByRole('button', { name: 'Refresh', exact: true }),
    ).toBeEnabled();
    const response = await request.post('/api/extensions/rescan', {
      data: { expected_revision: 0 },
    });
    expect(response.status()).toBe(403);
  });
});
