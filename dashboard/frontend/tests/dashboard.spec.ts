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

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
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
  await expect(
    page.locator('.scard').filter({ hasText: 'Frontend parity work' }),
  ).toBeVisible();
  const waiting = page.locator('.scard').filter({
    hasText: 'Waiting for subagent',
  });
  await expect(waiting).toHaveAttribute('data-tab', 'awaiting_background');
  await expect(waiting.locator('.badge')).toContainText('running');
  await expect(waiting.locator('.badge .st')).toHaveCSS(
    'background-color',
    'rgb(97, 175, 239)',
  );
  await expect(waiting.locator('.badge .st')).not.toHaveCSS(
    'background-color',
    'rgb(152, 195, 121)',
  );
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

  const backgroundRow = page.locator('.ol').filter({ has: background });
  const markerOffset = await backgroundRow.evaluate((row) => {
    const marker = row.querySelector<HTMLElement>('.anmark');
    if (marker === null)
      throw new Error('the background row has no status dot');
    const rowBounds = row.getBoundingClientRect();
    const markerBounds = marker.getBoundingClientRect();
    const rowCenter = rowBounds.top + rowBounds.height / 2;
    const markerCenter = markerBounds.top + markerBounds.height / 2;
    return Math.abs(rowCenter - markerCenter);
  });
  expect(markerOffset).toBeLessThanOrEqual(1);

  expect(failures).toEqual([]);
});

test('loads older activity when the feed bottom enters the viewport', async ({
  page,
  request,
}) => {
  const failures = watchBrowserFailures(page);
  const initialResponse = await request.get(
    '/sessionData/fixture-active/entries?limit=40',
  );
  expect(initialResponse.ok()).toBe(true);
  const initialPage: unknown = await initialResponse.json();
  if (!isRecord(initialPage))
    throw new Error('the entry fixture is not an object');

  await page.addInitScript((fixture: Record<string, unknown>) => {
    const originalFetch = window.fetch.bind(window);
    const testWindow = window as Window & {
      finishOlderPageRequest?: () => void;
      olderPageRequests?: number;
    };
    testWindow.olderPageRequests = 0;
    window.fetch = async (input, init): Promise<Response> => {
      const requestUrl =
        typeof input === 'string' || input instanceof URL
          ? new URL(input, window.location.href)
          : new URL(input.url);
      if (requestUrl.pathname !== '/sessionData/fixture-active/entries')
        return originalFetch(input, init);
      if (!requestUrl.searchParams.has('before'))
        return Response.json({ ...fixture, oldest_cursor: 1, has_more: true });

      const count = (testWindow.olderPageRequests ?? 0) + 1;
      testWindow.olderPageRequests = count;
      if (count > 1)
        return Response.json({
          items: [],
          oldest_cursor: 0,
          has_more: false,
        });
      return new Promise<Response>((resolve) => {
        testWindow.finishOlderPageRequest = () => {
          resolve(
            Response.json({
              items: null,
              oldest_cursor: 0,
              has_more: false,
            }),
          );
        };
      });
    };
  }, initialPage);

  await page.goto('/#/s/fixture-active');
  await expect(page.getByRole('button', { name: /load older/i })).toHaveCount(
    0,
  );

  const sentinel = page.locator('.load-sentinel');
  await sentinel.scrollIntoViewIfNeeded();
  await expect(
    page.getByRole('status', { name: 'loading older activity' }),
  ).toBeVisible();
  await page.evaluate(() => {
    window.scrollTo(0, document.body.scrollHeight);
  });
  await expect
    .poll(() =>
      page.evaluate(
        () =>
          (window as Window & { olderPageRequests?: number })
            .olderPageRequests ?? 0,
      ),
    )
    .toBe(1);

  await page.evaluate(() => {
    const finish = (window as Window & { finishOlderPageRequest?: () => void })
      .finishOlderPageRequest;
    if (finish === undefined)
      throw new Error('the older page request did not start');
    finish();
  });
  const retry = page.getByRole('button', { name: 'retry' });
  await expect(retry).toBeVisible();
  await retry.click();
  await expect
    .poll(() =>
      page.evaluate(
        () =>
          (window as Window & { olderPageRequests?: number })
            .olderPageRequests ?? 0,
      ),
    )
    .toBe(2);
  await expect(sentinel).toHaveCount(0);
  expect(failures).toEqual([]);
});

test('starts rewind target selection without opening a native menu', async ({
  page,
}) => {
  const failures = watchBrowserFailures(page);
  let nativeOpenRequests = 0;
  page.on('request', (request) => {
    if (request.url().includes('/controls/open-rewind'))
      nativeOpenRequests += 1;
  });
  await page.route('**/sessionData/fixture-active', async (route) => {
    const response = await route.fetch();
    const value: unknown = await response.json();
    if (!isRecord(value) || !Array.isArray(value.actors))
      throw new Error('the session fixture has no actor list');
    const actors = value.actors.map((actor: unknown) =>
      isRecord(actor) && actor.actor_id === 'fixture-active:lead'
        ? { ...actor, status: 'awaiting_response' }
        : actor,
    );
    const body = { ...value, live: true, actors };
    await route.fulfill({ response, json: body });
  });

  await page.goto('/#/s/fixture-active');
  const rewind = page.getByRole('button', { name: '↶ rewind' });
  await expect(rewind).toBeEnabled();
  await rewind.click();

  await expect(page.locator('.stream')).toHaveClass(/rwpick/);
  expect(nativeOpenRequests).toBe(0);
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
  const recordedAnswer = page.locator('.msg.answer').filter({
    hasText: 'All 120',
  });
  await expect(recordedAnswer.locator('.ansqt')).toHaveText([
    'Which incidents do I close to Done?',
    'Add a comment on each closed incident?',
  ]);
  await expect(recordedAnswer).not.toContainText('you ▸ answered0All 120');
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
    maxDiffPixels: 10,
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
  await expect(page.getByText('3 sessions all-time')).toBeVisible();
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
