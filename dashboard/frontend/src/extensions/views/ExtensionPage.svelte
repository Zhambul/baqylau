<script lang="ts">
  import { formatRoute, type ExtensionPageRoute } from '../../app/route';
  import ExtensionViewMount from './ExtensionViewMount.svelte';
  import { webViewCatalog } from './web-view-catalog.svelte';

  let { route }: { route: ExtensionPageRoute } = $props();

  const catalog = webViewCatalog();
  const mount = $derived.by(() => {
    const view = catalog?.find(route.view) ?? null;
    const runtimeRevision = catalog?.runtimeRevision ?? null;
    if (view === null || runtimeRevision === null) return null;
    return {
      key: `${runtimeRevision}:${view.extension_id}:${view.view_id}:${route.workspaceId}`,
      view,
      runtimeRevision,
      scope: { kind: 'workspace', workspace_id: route.workspaceId } as const,
    };
  });
</script>

<p class="page-links">
  <a
    href={formatRoute({
      kind: 'extension-settings',
      extensionId: route.view.extensionId,
      workspaceId: route.workspaceId,
    })}>Workspace settings</a
  >
</p>
{#if mount === null}
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

<style>
  .page-links {
    margin: 0 0 8px;
    font-size: 12px;
  }
</style>
