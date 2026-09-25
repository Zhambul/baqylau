import type { FeedEntrySubject } from '@baqylau/extension-api';

import type { WebView } from '../../api/extension-views';
import type { Entry } from '../../entries/model';
import type { WebViewCatalog } from './web-view-catalog.svelte';

export type FeedReplacement = {
  readonly view: WebView;
  readonly subject: FeedEntrySubject;
};

/**
 * Find the extension view that draws one feed row. A core entry kind can be
 * replaced by any package; an extension entry type only by the package that
 * owns it, and its view gets the entry's document.
 */
export function feedReplacement(
  catalog: WebViewCatalog,
  entry: Entry,
): FeedReplacement | null {
  if (entry.type !== 'extension') {
    const view = catalog.replacementFor(entry.type, 'session');
    return view === null
      ? null
      : { view, subject: { entryId: entry.entryId, kind: entry.type } };
  }
  const view = catalog.replacementFor(entry.body.entryType, 'session');
  if (view?.extension_id !== entry.body.owner) return null;
  return {
    view,
    subject: {
      entryId: entry.entryId,
      kind: entry.body.entryType,
      document: entry.body.document,
    },
  };
}
