import { getContext, setContext } from 'svelte';

import { readWebViews, type WebView } from '../../api/extension-views';
import type { ExtensionViewRef } from '../../app/route';

const CATALOG = Symbol('web-view-catalog');
const REFRESH_MS = 15_000;

/** Keep the active web views of the published runtime; a new runtime replaces them. */
export class WebViewCatalog {
  views = $state<readonly WebView[]>([]);
  runtimeRevision = $state<string | null>(null);

  private requests = 0;
  private lifetime: AbortSignal | null = null;

  /** Apply only the newest request's reply; an older reply that ends later is stale. */
  async refresh(signal: AbortSignal): Promise<void> {
    this.requests += 1;
    const request = this.requests;
    const reply = await readWebViews(signal);
    if (request !== this.requests) return;
    if (reply.runtime_revision !== this.runtimeRevision) {
      this.views = reply.views;
      this.runtimeRevision = reply.runtime_revision;
    }
  }

  /** Refresh now and then on a fixed period until the signal aborts. */
  follow(signal: AbortSignal): void {
    this.lifetime = signal;
    const refresh = (): void => {
      this.refresh(signal).catch(() => undefined);
    };
    refresh();
    const timer = setInterval(refresh, REFRESH_MS);
    signal.addEventListener('abort', () => {
      clearInterval(timer);
    });
  }

  /**
   * Refresh at once for a caller that saw a new runtime. The request follows
   * the catalog's lifetime, so it continues when the caller's page closes.
   */
  refreshNow(): void {
    if (this.lifetime === null) return;
    this.refresh(this.lifetime).catch(() => undefined);
  }

  /** The additive views of one slot and scope kind, in the host order. */
  forSlot(
    slot: WebView['slot'],
    scopeKind: WebView['scopes'][number],
  ): readonly WebView[] {
    return this.views.filter(
      (view) =>
        view.slot === slot &&
        view.mode === 'add' &&
        view.scopes.includes(scopeKind),
    );
  }

  /**
   * The view that replaces the core feed row of one entry kind. The runtime
   * switch refuses two replacements of one scoped target, so there is at most one.
   */
  replacementFor(
    entryKind: string,
    scopeKind: WebView['scopes'][number],
  ): WebView | null {
    return (
      this.views.find(
        (view) =>
          view.slot === 'feed' &&
          view.mode === 'replace' &&
          view.target === entryKind &&
          view.scopes.includes(scopeKind),
      ) ?? null
    );
  }

  find(reference: ExtensionViewRef): WebView | null {
    return (
      this.views.find(
        (view) =>
          view.extension_id === reference.extensionId &&
          view.view_id === reference.viewId,
      ) ?? null
    );
  }
}

export function provideWebViewCatalog(catalog: WebViewCatalog): void {
  setContext(CATALOG, catalog);
}

/** Read the page's catalog; a component outside the application has none. */
export function webViewCatalog(): WebViewCatalog | undefined {
  return getContext<WebViewCatalog | undefined>(CATALOG);
}
