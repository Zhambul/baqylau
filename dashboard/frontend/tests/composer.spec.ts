import { expect, test } from './fixtures';

test('keeps an unfinished message across an immediate reload', async ({
  page,
}) => {
  await page.goto('/#/s/fixture-active');

  const composer = page.locator('textarea.cinput');
  await composer.fill('Do not lose this unfinished message.');
  await page.reload();

  await expect(page.locator('textarea.cinput')).toHaveValue(
    'Do not lose this unfinished message.',
  );
});

test('expands the session prompt without an input scrollbar', async ({
  page,
}) => {
  await page.goto('/#/s/fixture-active');

  const composer = page.locator('textarea.cinput');
  await composer.fill(
    Array.from(
      { length: 40 },
      (_, index) => `Complete prompt line ${String(index + 1)}.`,
    ).join('\n'),
  );

  const size = await composer.evaluate((textarea) => ({
    clientHeight: textarea.clientHeight,
    overflowY: getComputedStyle(textarea).overflowY,
    scrollHeight: textarea.scrollHeight,
    viewportHeight: innerHeight,
  }));
  expect(size.clientHeight).toBeGreaterThan(size.viewportHeight * 0.4);
  expect(size.clientHeight).toBeGreaterThanOrEqual(size.scrollHeight);
  expect(size.overflowY).toBe('hidden');
});
