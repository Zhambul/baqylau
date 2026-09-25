import { describe, expect, it, vi } from 'vitest';

import * as api from '../../api/extension-views';
import { actorId, entryId } from '../../app/domain-ids';
import type { Entry } from '../../entries/model';

import { feedReplacement } from './feed-replacement';
import { WebViewCatalog } from './web-view-catalog.svelte';

vi.mock('../../api/extension-views', () => ({ readWebViews: vi.fn() }));

const ENVELOPE = {
  entryId: entryId('entry-1'),
  cursor: 1,
  actorId: actorId('actor-1'),
  parentActorId: null,
  turnId: null,
  occurredAt: 1,
  summary: null,
} as const;

const OWN_ENTRY: Entry = {
  ...ENVELOPE,
  type: 'extension',
  body: {
    owner: 'test.adapters',
    entryType: 'test.adapters.invocation',
    sourceEventId: 'event-1',
    document: '{"invocation_id":"toolu_1.0"}',
  },
};

function replacing(extensionId: string, target: string): api.WebView {
  return {
    extension_id: extensionId,
    package_digest: 'a'.repeat(64),
    view_id: `${extensionId}.row`,
    title: 'Row',
    slot: 'feed',
    scopes: ['session'],
    mode: 'replace',
    target,
    order: 0,
    module_url: `/extensions/${extensionId}/digest/web/row.js`,
    style_urls: [],
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

describe('feed replacement', () => {
  it('gives the owner of an entry type its row, with the document', async () => {
    const view = replacing('test.adapters', 'test.adapters.invocation');
    const catalog = await catalogWith([view]);

    expect(feedReplacement(catalog, OWN_ENTRY)).toEqual({
      view,
      subject: {
        entryId: 'entry-1',
        kind: 'test.adapters.invocation',
        document: '{"invocation_id":"toolu_1.0"}',
      },
    });
  });

  it('refuses a view of another package for an entry type', async () => {
    const catalog = await catalogWith([
      replacing('test.other', 'test.adapters.invocation'),
    ]);

    expect(feedReplacement(catalog, OWN_ENTRY)).toBeNull();
  });

  it('keeps the core entry kind rule', async () => {
    const view = replacing('test.shell', 'turn_started');
    const catalog = await catalogWith([view]);
    const core: Entry = { ...ENVELOPE, type: 'turn_started', body: {} };

    expect(feedReplacement(catalog, core)).toEqual({
      view,
      subject: { entryId: 'entry-1', kind: 'turn_started' },
    });
  });
});
