import AxeBuilder from '@axe-core/playwright';
import type { Page } from '@playwright/test';

import { expect, test } from './fixtures';

const FIXTURE_TIME = 1_700_000_000_000;

test.beforeEach(async ({ page }) => {
  await page.clock.setFixedTime(FIXTURE_TIME);
});

function watchBrowserFailures(page: Page): readonly string[] {
  const failures: string[] = [];
  page.on('response', (response) => {
    if (response.status() >= 500)
      failures.push(`${String(response.status())} ${response.url()}`);
  });
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

test('recovers a launch after the hidden page loses its global stream', async ({
  page,
  request,
}) => {
  const failures = watchBrowserFailures(page);
  const listResponse = await request.get('/sessionData');
  expect(listResponse.ok()).toBe(true);
  const list: unknown = await listResponse.json();
  if (!isRecord(list) || !Array.isArray(list.sessions))
    throw new Error('the session list fixture is not an object');
  const sessions = list.sessions as unknown[];
  const source: unknown = sessions[0];
  if (!isRecord(source) || !isRecord(source.session))
    throw new Error('the session fixture is not an object');
  const sourceActors: unknown[] = Array.isArray(source.actors)
    ? (source.actors as unknown[])
    : [];

  await page.addInitScript(() => {
    type ControlledWindow = Window & {
      controlledEventSources?: ControlledEventSource[];
      setControlledVisibility?: (state: DocumentVisibilityState) => void;
      dropControlledGlobalStream?: () => void;
      emitControlledGlobalFrame?: (frame: unknown) => void;
    };

    class ControlledEventSource extends EventTarget {
      readonly url: string;
      onopen: ((event: Event) => void) | null = null;
      onerror: ((event: Event) => void) | null = null;
      closed = false;

      constructor(url: string | URL) {
        super();
        this.url = String(url);
        const controlled = window as ControlledWindow;
        controlled.controlledEventSources ??= [];
        controlled.controlledEventSources.push(this);
        setTimeout(() => {
          this.onopen?.(new Event('open'));
        }, 0);
      }

      close(): void {
        this.closed = true;
      }
    }

    const controlled = window as ControlledWindow;
    controlled.controlledEventSources = [];
    let visibility: DocumentVisibilityState = 'visible';
    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      get: () => visibility,
    });
    controlled.setControlledVisibility = (state) => {
      visibility = state;
      document.dispatchEvent(new Event('visibilitychange'));
    };
    controlled.dropControlledGlobalStream = () => {
      const stream = controlled.controlledEventSources?.find((candidate) =>
        candidate.url.includes('/sessionData/stream'),
      );
      if (stream === undefined)
        throw new Error('the global event stream did not open');
      stream.onerror?.(new Event('error'));
    };
    controlled.emitControlledGlobalFrame = (frame) => {
      const streams = controlled.controlledEventSources?.filter((candidate) =>
        candidate.url.includes('/sessionData/stream'),
      );
      const stream = streams?.at(-1);
      if (stream === undefined)
        throw new Error('the recovered global event stream did not open');
      stream.dispatchEvent(
        new MessageEvent('sessionData', { data: JSON.stringify(frame) }),
      );
    };
    Reflect.set(window, 'EventSource', ControlledEventSource);
  });

  await page.route('**/api/sessions', async (route) => {
    if (route.request().method() !== 'POST') {
      await route.fallback();
      return;
    }
    await route.fulfill({
      status: 202,
      json: { status: 'started', window_id: 'fixture-window', reason: null },
    });
  });
  await page.goto('/');
  await expect(page.locator('#conn')).toHaveAttribute('data-on', '1');
  await page.evaluate(() => {
    const controlled = window as Window & {
      setControlledVisibility?: (state: DocumentVisibilityState) => void;
      dropControlledGlobalStream?: () => void;
    };
    controlled.setControlledVisibility?.('hidden');
    controlled.dropControlledGlobalStream?.();
    controlled.setControlledVisibility?.('visible');
  });
  await expect
    .poll(() =>
      page.evaluate(
        () =>
          (
            window as Window & {
              controlledEventSources?: { url: string }[];
            }
          ).controlledEventSources?.filter((source) =>
            source.url.includes('/sessionData/stream'),
          ).length ?? 0,
      ),
    )
    .toBe(2);

  await page.evaluate((session: Record<string, unknown>) => {
    const controlled = window as Window & {
      emitControlledGlobalFrame?: (frame: unknown) => void;
    };
    controlled.emitControlledGlobalFrame?.({
      sessions: [{ ...session, state: 'finished', finished_at: Date.now() }],
      actors: [],
    });
  }, source.session);
  await expect(
    page.locator('.scard').filter({ hasText: 'Frontend parity work' }),
  ).toHaveCount(0);

  const workingDirectory = String(source.session.working_directory);
  await page.getByRole('button', { name: '+ session' }).click();
  const dialog = page.getByRole('dialog', { name: 'new session' });
  await dialog.getByLabel('directory').fill(workingDirectory);
  await dialog
    .getByPlaceholder(/what should .* start on\?/)
    .fill('Show the recovered session.');
  const launchResponse = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === '/api/sessions' &&
      response.request().method() === 'POST',
  );
  await dialog.getByRole('button', { name: 'launch', exact: true }).click();
  await launchResponse;
  await expect(page.getByText('starting session')).toBeVisible();

  await page.evaluate(
    (frame) => {
      const controlled = window as Window & {
        emitControlledGlobalFrame?: (frame: unknown) => void;
      };
      controlled.emitControlledGlobalFrame?.(frame);
    },
    { sessions: [source.session], actors: sourceActors },
  );

  await expect(page).toHaveURL(/#\/s\/fixture-active$/);
  await expect(page.locator('.shead .proj')).toHaveText('Frontend parity work');
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
  const directory = dialog.getByLabel('directory');
  await expect(directory).toHaveAttribute('autocomplete', 'off');
  await directory.fill(workingDirectory);
  await expect(
    dialog.getByRole('option', { name: workingDirectory }),
  ).toBeVisible();
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

test('keeps the active resume row visible during keyboard navigation', async ({
  page,
}) => {
  const failures = watchBrowserFailures(page);
  await page.addInitScript(() => {
    const nativeFetch = window.fetch.bind(window);
    window.fetch = async (...arguments_) => {
      const input = arguments_[0];
      const requestUrl =
        input instanceof Request ? input.url : input.toString();
      const url = new URL(requestUrl, window.location.origin);
      if (url.pathname !== '/api/resumable-sessions')
        return nativeFetch(...arguments_);
      return new Response(
        JSON.stringify(
          Array.from({ length: 20 }, (_, index) => ({
            session_id: `history-${String(index)}`,
            title: `History ${String(index)}`,
            last_activity_at: 100 - index,
            active: false,
            harness: 'codex',
            model: null,
            effort: null,
            account: null,
          })),
        ),
        { headers: { 'Content-Type': 'application/json' } },
      );
    };
  });
  await page.goto('/');
  await page.getByRole('button', { name: '+ session' }).click();
  const dialog = page.getByRole('dialog', { name: 'new session' });
  await dialog.getByText('fresh conversation').click();
  const search = dialog.getByPlaceholder(
    'search all sessions in this directory…',
  );
  const list = dialog.getByRole('listbox', { name: 'sessions to resume' });
  await expect(list.getByRole('option')).toHaveCount(20);

  for (let index = 0; index < 12; index += 1) await search.press('ArrowDown');

  const active = list.locator('.nsresrow.sel');
  await expect(active).toHaveAttribute('data-session-id', 'history-12');
  const position = await active.evaluate((row) => {
    const list = row.parentElement;
    if (list === null) throw new Error('resume list is missing');
    const listBounds = list.getBoundingClientRect();
    const rowBounds = row.getBoundingClientRect();
    return {
      scrollTop: list.scrollTop,
      listTop: listBounds.top,
      listBottom: listBounds.bottom,
      rowTop: rowBounds.top,
      rowBottom: rowBounds.bottom,
    };
  });
  expect(position.scrollTop).toBeGreaterThan(0);
  expect(position.rowTop).toBeGreaterThanOrEqual(position.listTop - 1);
  expect(position.rowBottom).toBeLessThanOrEqual(position.listBottom + 1);
  expect(failures).toEqual([]);
});
