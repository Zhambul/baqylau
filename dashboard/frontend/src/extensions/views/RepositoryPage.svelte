<script lang="ts">
  import type { ExtensionScope } from '@baqylau/extension-api';

  import { readRepositoryScope } from '../../api/extension-views';
  import type { RepositoryPageRoute } from '../../app/route';
  import ExtensionViewMount from './ExtensionViewMount.svelte';
  import { webViewCatalog } from './web-view-catalog.svelte';

  let { route }: { route: RepositoryPageRoute } = $props();

  const catalog = webViewCatalog();
  // Raw state: the view host clones the scope, and a deep state proxy cannot be cloned.
  let scope = $state.raw<ExtensionScope | null>(null);
  let phase = $state<'loading' | 'ready' | 'none'>('loading');

  // The daemon names the repository with the SDK's rule, so the page and the
  // extension name one repository the same way.
  $effect(() => {
    const controller = new AbortController();
    phase = 'loading';
    void readRepositoryScope(route.directory, controller.signal)
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

  const mount = $derived.by(() => {
    const view = catalog?.find(route.view) ?? null;
    const runtimeRevision = catalog?.runtimeRevision ?? null;
    if (view === null || runtimeRevision === null || scope === null) {
      return null;
    }
    const key = `${runtimeRevision}:${view.extension_id}:${view.view_id}:${JSON.stringify(scope)}`;
    return { key, view, runtimeRevision, scope };
  });
</script>

{#if phase === 'loading'}
  <div class="waiting" role="status">Finding the repository…</div>
{:else if phase === 'none'}
  <div class="empty" role="status">
    {route.directory} is not in a Git repository.
  </div>
{:else if mount === null}
  <div class="empty" role="status">This extension view is not available.</div>
{:else}
  {#key mount.key}
    <ExtensionViewMount
      view={mount.view}
      scope={mount.scope}
      runtimeRevision={mount.runtimeRevision}
    />
  {/key}
{/if}
