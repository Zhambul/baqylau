<script lang="ts">
  import type {
    ExtensionScope,
    FeedEntrySubject,
  } from '@baqylau/extension-api';
  import { ExtensionViewHost } from '@baqylau/extension-api/host';
  import { untrack } from 'svelte';

  import { readViewSettings, type WebView } from '../../api/extension-views';
  import {
    hostClient,
    pageTheme,
    viewBundle,
    viewSnapshot,
  } from './view-context';

  let {
    view,
    scope,
    runtimeRevision,
    subject,
    importModule,
  }: {
    view: WebView;
    scope: ExtensionScope;
    runtimeRevision: string;
    subject?: FeedEntrySubject;
    importModule?: (url: string) => Promise<unknown>;
  } = $props();

  let target: HTMLElement | undefined = $state();
  let host: ExtensionViewHost | undefined = $state();
  let failure = $state(false);

  // One host per mount target. The SDK host disposes the previous view when it
  // shows the next one, and when the target goes away.
  $effect(() => {
    if (target === undefined) return;
    const created = new ExtensionViewHost({
      target,
      origin: window.location.origin,
      createClient: (snapshot, signal) => hostClient(snapshot, signal),
      reportFailure: () => {
        failure = true;
      },
      ...(importModule === undefined ? {} : { importModule }),
    });
    host = created;
    return () => {
      host = undefined;
      void created.clear();
    };
  });

  // A changed view, scope, subject, or runtime revision is a new mount. A
  // parent can pass equal new objects on each update; the identity text
  // changes only when a value changes.
  const identity = $derived(
    JSON.stringify([
      view.extension_id,
      view.view_id,
      view.module_url,
      scope,
      runtimeRevision,
      subject ?? null,
    ]),
  );

  $effect(() => {
    if (host === undefined || target === undefined) return;
    void identity;
    const shown = host;
    const selected = untrack(() => ({
      view,
      scope,
      runtimeRevision,
      subject,
    }));
    const theme = pageTheme(target);
    const controller = new AbortController();
    failure = false;
    void readViewSettings(
      selected.view.extension_id,
      JSON.stringify(selected.scope),
      controller.signal,
    )
      .then((reply) => {
        // A settings reply can arrive after this run stopped; show only
        // the current selection.
        if (controller.signal.aborted) return;
        return shown.show(
          viewBundle(selected.view),
          viewSnapshot(
            selected.view,
            selected.scope,
            selected.runtimeRevision,
            reply,
            theme,
            selected.subject,
          ),
        );
      })
      .catch(() => {
        if (!controller.signal.aborted) failure = true;
      });
    return () => {
      controller.abort();
    };
  });
</script>

<section class="extension-view" aria-label={view.title}>
  <div bind:this={target} class="mount"></div>
  {#if failure}
    <p role="status" class="failure">{view.title} is not available.</p>
  {/if}
</section>

<style>
  .extension-view {
    display: flex;
    flex-direction: column;
    min-height: 0;
  }

  .mount {
    min-height: 0;
  }

  .failure {
    color: var(--dim);
  }
</style>
