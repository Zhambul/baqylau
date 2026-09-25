import { describe, expect, it, vi } from 'vitest';

import * as api from '../../api/extension-views';

import { WebViewCatalog } from './web-view-catalog.svelte';

vi.mock('../../api/extension-views', () => ({ readWebViews: vi.fn() }));

function webView(overrides: Partial<api.WebView>): api.WebView {
  return {
    extension_id: 'test.shell',
    package_digest: 'a'.repeat(64),
    view_id: 'test.shell.view',
    title: 'Shell',
    slot: 'feed',
    scopes: ['session'],
    mode: 'add',
    target: null,
    order: 0,
    module_url: '/extensions/test.shell/digest/web/main.js',
    style_urls: [],
    ...overrides,
  };
}

async function catalogWith(
  views: readonly api.WebView[],
): Promise<WebViewCatalog> {
  vi.mocked(api.readWebViews).mockResolvedValue({
    runtime_revision: 'runtime-one',
    views: [...views],
  });
  const catalog = new WebViewCatalog();
  await catalog.refresh(new AbortController().signal);
  return catalog;
}

describe('web view catalog', () => {
  it('lists only additive views for a slot and scope', async () => {
    const decoration = webView({ view_id: 'test.shell.note' });
    const catalog = await catalogWith([
      decoration,
      webView({ mode: 'replace', target: 'shell_started' }),
      webView({ view_id: 'test.shell.page', scopes: ['workspace'] }),
    ]);

    expect(catalog.forSlot('feed', 'session')).toEqual([decoration]);
    expect(catalog.forSlot('toolbar', 'session')).toEqual([]);
  });

  it('finds the replacement of one entry kind in one scope', async () => {
    const replacement = webView({ mode: 'replace', target: 'shell_started' });
    const catalog = await catalogWith([replacement]);

    expect(catalog.replacementFor('shell_started', 'session')).toEqual(
      replacement,
    );
    expect(catalog.replacementFor('shell_output', 'session')).toBeNull();
    expect(catalog.replacementFor('shell_started', 'workspace')).toBeNull();
  });

  it('keeps the newer runtime when an older reply ends later', async () => {
    const replies: ((value: api.WebViews) => void)[] = [];
    vi.mocked(api.readWebViews).mockImplementation(
      () =>
        new Promise((resolve) => {
          replies.push(resolve);
        }),
    );
    const catalog = new WebViewCatalog();
    const signal = new AbortController().signal;
    const older = catalog.refresh(signal);
    const newer = catalog.refresh(signal);
    const [resolveOlder, resolveNewer] = replies;

    resolveNewer?.({ runtime_revision: 'runtime-two', views: [] });
    await newer;
    resolveOlder?.({ runtime_revision: 'runtime-one', views: [webView({})] });
    await older;

    expect(catalog.runtimeRevision).toBe('runtime-two');
    expect(catalog.views).toEqual([]);
  });

  it('refreshes now with its own lifetime, not the caller page', async () => {
    vi.mocked(api.readWebViews).mockClear();
    vi.mocked(api.readWebViews).mockResolvedValue({
      runtime_revision: 'runtime-one',
      views: [],
    });
    const catalog = new WebViewCatalog();
    catalog.refreshNow();
    expect(api.readWebViews).not.toHaveBeenCalled();

    const lifetime = new AbortController();
    catalog.follow(lifetime.signal);
    vi.mocked(api.readWebViews).mockClear();
    vi.mocked(api.readWebViews).mockResolvedValue({
      runtime_revision: 'runtime-two',
      views: [webView({})],
    });
    catalog.refreshNow();
    await vi.waitFor(() => {
      expect(catalog.runtimeRevision).toBe('runtime-two');
    });
    expect(api.readWebViews).toHaveBeenCalledWith(lifetime.signal);
    lifetime.abort();
  });
});
