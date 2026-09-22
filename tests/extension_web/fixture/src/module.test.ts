import type { ExtensionViewContext } from '@baqylau/extension-api';
import { flushSync } from 'svelte';
import { expect, it } from 'vitest';

import { mount } from './index.svelte.js';

it('owns its component updates and releases its window listener', async () => {
  const target = document.createElement('div');
  const controller = new AbortController();
  const context: ExtensionViewContext = {
    extensionId: 'test.views',
    viewId: 'test.views.main',
    runtimeRevision: 'runtime-1',
    settingsRevision: 0,
    settings: null,
    scope: { kind: 'installation' },
    theme: {
      mode: 'dark',
      background: '#111111',
      foreground: '#ffffff',
      accent: '#00aaff',
      muted: '#999999',
      fontFamily: 'monospace',
      fontSize: '14px',
    },
    signal: controller.signal,
    api: {
      listExtensions: () =>
        Promise.resolve({
          catalog_revision: 1,
          runtime_revision: 'runtime-1',
          entries: [],
        }),
    },
  };
  const view = await mount(target, context);
  flushSync();
  await view.update({ ...context, settingsRevision: 2 });
  flushSync();
  expect(
    target.querySelector('[data-testid="settings"]')?.textContent,
  ).toContain('Settings revision: 2');
  window.dispatchEvent(new Event('fixture-pulse'));
  flushSync();
  const counter = target.querySelector('[data-testid="pulses"]');
  expect(counter?.textContent).toBe('Pulses: 1');
  await view.dispose();
  window.dispatchEvent(new Event('fixture-pulse'));
  flushSync();
  expect(counter?.textContent).toBe('Pulses: 1');
  expect(target.querySelector('section')).toBeNull();
});
