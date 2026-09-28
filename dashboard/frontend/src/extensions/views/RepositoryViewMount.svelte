<script lang="ts">
  import type { ExtensionScope } from '@baqylau/extension-api';

  import { readRepositoryScope, type WebView } from '../../api/extension-views';
  import ExtensionViewMount from './ExtensionViewMount.svelte';

  let {
    view,
    directory,
    runtimeRevision,
  }: { view: WebView; directory: string; runtimeRevision: string } = $props();

  // Raw state: the view host clones the scope, and a deep state proxy cannot be cloned.
  let scope = $state.raw<ExtensionScope | null>(null);
  let phase = $state<'loading' | 'ready' | 'none'>('loading');

  // The daemon names the repository with the SDK's rule, so the page and the
  // extension name one repository the same way.
  $effect(() => {
    const controller = new AbortController();
    phase = 'loading';
    void readRepositoryScope(directory, controller.signal)
      .then((found) => {
        scope = found;
        phase = found === null ? 'none' : 'ready';
      })
      .catch(() => {
        if (!controller.signal.aborted) phase = 'none';
      });
    return () => {
      controller.abort();
    };
  });

  const key = $derived(
    `${runtimeRevision}:${view.extension_id}:${view.view_id}:${JSON.stringify(scope)}`,
  );
</script>

{#if phase === 'loading'}
  <div class="waiting" role="status">Finding the repository…</div>
{:else if phase === 'none' || scope === null}
  <div class="empty" role="status">
    {directory} is not in a Git repository.
  </div>
{:else}
  {#key key}
    <ExtensionViewMount {view} {scope} {runtimeRevision} />
  {/key}
{/if}
