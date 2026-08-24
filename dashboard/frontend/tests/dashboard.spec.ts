import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';

test.describe.configure({ mode: 'serial' });

function watchBrowserFailures(page: Page): readonly string[] {
  const failures: string[] = [];
  page.on('console', (message) => {
    const text = message.text();
    // WebKit ignores this Chromium viewport extension and reports the ignore
    // as a console error. Safari still uses the rest of the viewport contract.
    if (text.includes('interactive-widget') && text.includes('not recognized'))
      return;
    if (message.type() === 'error') failures.push(text);
  });
  page.on('pageerror', (error) => {
    failures.push(error.message);
  });
  return failures;
}

async function expectAccessible(page: Page): Promise<void> {
  // The established design uses deliberately low-contrast tertiary metadata.
  // Keep that visual contract separate from structural accessibility failures.
  const scan = await new AxeBuilder({ page })
    .disableRules(['color-contrast'])
    .analyze();
  expect(
    scan.violations.filter(
      (violation) =>
        violation.impact === 'critical' || violation.impact === 'serious',
    ),
  ).toEqual([]);
}

test('loads the production shell and session list without browser failures', async ({
  page,
  request,
}) => {
  const failures = watchBrowserFailures(page);
  const response = await page.goto('/');

  expect(response?.status()).toBe(200);
  expect(response?.headers()['content-security-policy']).toContain(
    "script-src 'self' blob:",
  );
  await expect(page.getByRole('link', { name: 'baqylau' })).toBeVisible();
  const brandMark = page.locator('.brandmark');
  await expect(brandMark).toBeVisible();
  await expect(brandMark.locator('line')).toHaveCount(8);
  await expect(brandMark.locator('circle')).toHaveCount(11);
  const favicon = await request.get('/favicon.ico');
  expect(favicon.status()).toBe(200);
  expect(favicon.headers()['content-type']).toContain(
    'image/vnd.microsoft.icon',
  );
  expect((await favicon.body()).subarray(0, 4)).toEqual(
    Buffer.from([0, 0, 1, 0]),
  );
  await expect(page.getByText('2 sessions')).toBeVisible();

  await page.getByRole('button', { name: /parked · 2/ }).click();
  await expect(page.getByText('Frontend parity work')).toBeVisible();
  await expect(page.getByText('Finished migration research')).toBeVisible();
  await expect(page.locator('#conn')).toHaveAttribute('data-on', '1');

  await expectAccessible(page);
  await expect(page).toHaveScreenshot('session-list.png', {
    fullPage: true,
  });
  expect(failures).toEqual([]);
});

test('keeps activity details readable and expandable', async ({ page }) => {
  const failures = watchBrowserFailures(page);
  await page.goto('/#/s/fixture-active');

  const queued = page.locator('.msg.prompt.queued');
  await expect(queued).toContainText('show this complete queued message');
  await expect(queued.locator('.qbadge')).toHaveText('⧗ queued');

  const edit = page.locator('.blk').filter({
    has: page.locator('.bchips', {
      hasText: 'Edit(dashboard/frontend/src/app/App.svelte)',
    }),
  });
  await expect(edit).toHaveAttribute('data-open', '0');
  await edit.locator('.bhead').click();
  await expect(edit).toHaveAttribute('data-open', '1');
  await expect(edit.locator('.tdiff')).toContainText('typed shell');

  const summaries = page.locator('.vsum');
  for (let index = 0; index < (await summaries.count()); index += 1)
    await summaries.nth(index).click();
  const longCommand = page.locator('.blk').filter({
    hasText: 'every-frontend-operation',
  });
  await expect(longCommand).toHaveAttribute('data-open', '0');
  await longCommand.locator('.bhead').click();
  await expect(longCommand).toHaveAttribute('data-open', '1');
  const summary = longCommand.locator('.bsum');
  await expect(summary).toContainText('every-frontend-operation');
  await expect(summary).toHaveCSS('white-space', 'pre-wrap');
  await expect(summary).toHaveCSS('text-overflow', 'clip');

  const webSearch = page.locator('.operation-label', {
    hasText: 'WebSearch',
  });
  const background = page.locator('.operation-label', {
    hasText: 'background',
  });
  await expect(webSearch).toHaveCSS('font-weight', '500');
  await expect(background).toHaveCSS('font-weight', '500');
  const labelStyles = await Promise.all(
    [webSearch, background].map((label) =>
      label.evaluate((element) => {
        const style = getComputedStyle(element);
        return { color: style.color, background: style.backgroundColor };
      }),
    ),
  );
  expect(labelStyles[0]?.color).toBe(labelStyles[1]?.color);
  expect(labelStyles[0]?.color).not.toBe('rgb(20, 22, 28)');
  expect(
    labelStyles.every((style) => style.background === 'rgba(0, 0, 0, 0)'),
  ).toBe(true);

  expect(failures).toEqual([]);
});

test('preserves the session, agent, monitor, and statistics routes', async ({
  page,
}) => {
  const failures = watchBrowserFailures(page);
  await page.goto('/#/s/fixture-active');

  await expect(page.locator('.shead .proj')).toHaveText('Frontend parity work');
  await expect(page.locator('.askcard .askqtext')).toHaveText(
    'How should the old entry be retired?',
  );
  await expect(page.locator('.rchip.rk-monitor')).toHaveText(/monitor/);
  await expect(page.getByText('Audit the old router')).toBeVisible();
  await expect(page.getByRole('button', { name: '↶ rewind' })).toBeDisabled();
  await expect(page.getByRole('button', { name: '↶ rewind' })).toHaveAttribute(
    'title',
    'rewind: pick a message to restore to',
  );
  await expect(
    page.getByRole('button', { name: '◷ background' }),
  ).toBeDisabled();
  await expect(
    page.getByRole('button', { name: '◷ background' }),
  ).toHaveAttribute('title', "not supported by this session's tool");
  await expect(
    page.getByRole('link', { name: 'errors', exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText('The rewrite uses Svelte 5 with strict TypeScript'),
  ).toBeVisible();
  await expect(page).toHaveScreenshot('session-mirror.png', {
    fullPage: true,
  });

  await page.getByRole('link', { name: /Audit the old router/ }).click();
  await expect(page).toHaveURL(/\/a\/fixture-active%3Aresearcher$/);
  await expect(page.locator('.shead .proj')).toHaveText(
    '◇ Audit the old router',
  );
  await expect(page.getByRole('link', { name: '← session' })).toBeVisible();
  await expect(page.getByRole('button', { name: /rename/ })).toHaveCount(0);
  await expect(page.locator('textarea.cinput')).toHaveCount(0);

  await page.getByRole('link', { name: /agents/ }).click();
  await expect(page).toHaveURL(/\/agents$/);
  await expect(page.locator('.shead .proj')).toHaveText('Frontend parity work');
  await expect(page.locator('.sgrid .actorId')).toHaveText(
    '◇ Audit the old router',
  );

  await page.getByRole('link', { name: /monitors/ }).click();
  await expect(page.getByText('frontend type checks')).toBeVisible();
  await page.getByRole('link', { name: /frontend type checks/ }).click();
  await expect(page.getByText('watching for changes')).toBeVisible();

  await page.goto('/#/stats');
  await expect(page.getByRole('heading', { name: 'Insights' })).toBeVisible();
  await expect(page.getByText('2 sessions all-time')).toBeVisible();
  await expectAccessible(page);
  expect(failures).toEqual([]);
});

test('keeps the new-session and resume-preview modal boundaries', async ({
  page,
}) => {
  const failures = watchBrowserFailures(page);
  await page.goto('/');
  const workingDirectory = await page.locator('.dirpath').innerText();
  await page.getByRole('button', { name: '+ session' }).click();

  const dialog = page.getByRole('dialog', { name: 'new session' });
  await expect(dialog).toBeVisible();
  await dialog.getByLabel('directory').fill(workingDirectory);
  await dialog.getByText('fresh conversation').click();
  await expect(dialog.getByText('resume a conversation')).toBeVisible();
  const search = dialog.getByPlaceholder(
    'search all sessions in this directory…',
  );
  await search.fill('Frontend parity');
  const row = dialog.getByText('Frontend parity work');
  await expect(row).toBeVisible();
  await row.dblclick();

  const preview = page.getByRole('dialog', {
    name: 'Preview Frontend parity work',
  });
  await expect(preview).toBeVisible();
  await expect(preview.getByText('The rewrite uses Svelte 5')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(preview).toHaveCount(0);
  await expect(dialog).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);

  expect(failures).toEqual([]);
});
