import { expect, test } from './fixtures';

test('uses model effort values and omits unsupported effort at launch', async ({
  page,
}) => {
  await page.route('**/api/harnesses/*/catalog*', async (route) => {
    await route.fulfill({
      json: {
        commands: [],
        rewind_modes: [],
        models: [
          {
            model_id: 'low-model',
            display_name: 'Low model',
            default: true,
            efforts: [{ value: 'low', display_name: 'low', default: true }],
          },
          {
            model_id: 'high-model',
            display_name: 'High model',
            default: false,
            efforts: [{ value: 'high', display_name: 'high', default: true }],
          },
          {
            model_id: 'no-effort-model',
            display_name: 'No effort model',
            default: false,
            efforts: [],
          },
        ],
      },
    });
  });
  await page.route('**/api/sessions', async (route) => {
    if (route.request().method() !== 'POST') return route.fallback();
    await route.fulfill({
      status: 202,
      json: { status: 'started', window_id: 'model-test', reason: null },
    });
  });
  await page.goto('/');
  await page.getByRole('button', { name: '+ session', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'new session' });
  const model = dialog.getByRole('button', { name: 'model', exact: true });
  const effort = dialog.getByRole('button', { name: 'effort', exact: true });
  await expect(effort).toContainText('low');
  await model.click();
  await dialog.getByRole('option', { name: 'High model', exact: true }).click();
  await expect(effort).toContainText('high');
  await model.click();
  await dialog
    .getByRole('option', { name: 'No effort model', exact: true })
    .click();
  await expect(effort).toHaveCount(0);
  await dialog
    .getByRole('textbox', { name: 'directory', exact: true })
    .fill('/tmp');
  await dialog
    .getByRole('textbox', { name: 'first prompt', exact: false })
    .fill('Start this model.');
  const submitted = page.waitForRequest(
    (request) =>
      request.method() === 'POST' &&
      new URL(request.url()).pathname === '/api/sessions',
  );
  await dialog.getByRole('button', { name: 'launch', exact: true }).click();
  expect((await submitted).postDataJSON()).toMatchObject({
    model_id: 'no-effort-model',
    effort: null,
  });
});
