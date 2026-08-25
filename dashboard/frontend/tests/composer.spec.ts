import { expect, test } from '@playwright/test';

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
