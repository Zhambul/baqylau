import { expect, test } from './fixtures';

test('shares goal dismissal with another device and a new browser', async ({
  page,
  browser,
  baseURL,
}) => {
  if (baseURL === undefined) throw new Error('The fixture URL is missing');
  const otherDevice = await browser.newContext({ baseURL });
  try {
    const otherPage = await otherDevice.newPage();
    await page.goto('/#/s/fixture-parked');
    await otherPage.goto('/#/s/fixture-parked');
    await expect(page.getByText('Completed goal marker')).toBeVisible();
    await expect(otherPage.getByText('Completed goal marker')).toBeVisible();
    await page.getByRole('button', { name: 'Dismiss completed goal' }).click();
    await expect(page.getByText('Completed goal marker')).toBeVisible();
    await page
      .getByRole('button', { name: 'Confirm dismiss completed goal' })
      .click();
    await expect(page.getByText('Completed goal marker')).toHaveCount(0);
    await expect(otherPage.getByText('Completed goal marker')).toHaveCount(0);
    await page.reload();
    await expect(page.locator('.stream')).toBeVisible();
    await expect(page.getByText('Completed goal marker')).toHaveCount(0);
    const freshDevice = await browser.newContext({ baseURL });
    try {
      const freshPage = await freshDevice.newPage();
      await freshPage.goto('/#/s/fixture-parked');
      await expect(freshPage.locator('.stream')).toBeVisible();
      await expect(freshPage.getByText('Completed goal marker')).toHaveCount(0);
    } finally {
      await freshDevice.close();
    }
  } finally {
    await otherDevice.close();
  }
});
