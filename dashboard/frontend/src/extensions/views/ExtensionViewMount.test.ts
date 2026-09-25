import { render, screen, waitFor } from '@testing-library/svelte';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import * as api from '../../api/extension-views';

import ExtensionViewMount from './ExtensionViewMount.svelte';

vi.mock('../../api/extension-views', () => ({ readViewSettings: vi.fn() }));

const DIGEST = 'a'.repeat(64);
const view: api.WebView = {
  extension_id: 'test.logs',
  package_digest: DIGEST,
  view_id: 'test.logs.main',
  title: 'Logs',
  slot: 'session_tab',
  scopes: ['installation'],
  mode: 'add',
  target: null,
  order: 0,
  module_url: `/extensions/test.logs/${DIGEST}/web/main.js`,
  style_urls: [],
};
const scope = { kind: 'installation' } as const;

function mountedText(): string {
  const root = document.querySelector('[data-extension-view]');
  return root?.shadowRoot?.textContent ?? '';
}

function module(disposed: string[], mounts: string[] = []): unknown {
  return {
    mount: (target: HTMLElement, context: { runtimeRevision: string }) => {
      mounts.push(context.runtimeRevision);
      target.textContent = `mounted ${context.runtimeRevision}`;
      return {
        update: () => undefined,
        dispose: () => {
          disposed.push(context.runtimeRevision);
        },
      };
    },
  };
}

describe('extension view mount', () => {
  beforeEach(() => {
    vi.mocked(api.readViewSettings).mockResolvedValue({
      settingsRevision: 3,
      settings: null,
    });
  });

  it('mounts the package module and disposes it on a new runtime', async () => {
    const disposed: string[] = [];
    const importModule = vi.fn(() => Promise.resolve(module(disposed)));
    const mounted = render(ExtensionViewMount, {
      view,
      scope,
      runtimeRevision: 'runtime-one',
      importModule,
    });

    await waitFor(() => {
      expect(mountedText()).toBe('mounted runtime-one');
    });
    await mounted.rerender({
      view,
      scope,
      runtimeRevision: 'runtime-two',
      importModule,
    });
    await waitFor(() => {
      expect(mountedText()).toBe('mounted runtime-two');
    });
    expect(disposed).toEqual(['runtime-one']);
    mounted.unmount();
    await waitFor(() => {
      expect(disposed).toEqual(['runtime-one', 'runtime-two']);
    });
  });

  it('keeps one root and one live mount after repeated reloads', async () => {
    const disposed: string[] = [];
    const mounts: string[] = [];
    const importModule = vi.fn(() => Promise.resolve(module(disposed, mounts)));
    const mounted = render(ExtensionViewMount, {
      view,
      scope,
      runtimeRevision: 'runtime-0',
      importModule,
    });
    const reloads = 5;

    for (let index = 1; index <= reloads; index += 1) {
      const runtimeRevision = `runtime-${String(index)}`;
      await mounted.rerender({ view, scope, runtimeRevision, importModule });
      await waitFor(() => {
        expect(mountedText()).toBe(`mounted ${runtimeRevision}`);
      });
    }

    // The first reload comes before the first import ends, so that view
    // never mounts. Every other mount except the last is disposed.
    expect(document.querySelectorAll('[data-extension-view]')).toHaveLength(1);
    expect(mounts.at(-1)).toBe(`runtime-${String(reloads)}`);
    expect(disposed).toEqual(mounts.slice(0, -1));
    mounted.unmount();
    await waitFor(() => {
      expect(disposed).toEqual(mounts);
    });
    expect(document.querySelectorAll('[data-extension-view]')).toHaveLength(0);
  });

  it('shows the unavailable state when the module fails', async () => {
    render(ExtensionViewMount, {
      view,
      scope,
      runtimeRevision: 'runtime-one',
      importModule: () => Promise.resolve({}),
    });

    expect(
      await screen.findByText('Logs is not available.'),
    ).toBeInTheDocument();
  });
});
