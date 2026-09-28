<script lang="ts">
  import type { RepositoryPageRoute } from '../../app/route';
  import RepositoryViewMount from './RepositoryViewMount.svelte';
  import { webViewCatalog } from './web-view-catalog.svelte';

  let { route }: { route: RepositoryPageRoute } = $props();

  const catalog = webViewCatalog();
  const view = $derived(catalog?.find(route.view) ?? null);
  const runtimeRevision = $derived(catalog?.runtimeRevision ?? null);
</script>

{#if view === null || runtimeRevision === null}
  <div class="empty" role="status">This extension view is not available.</div>
{:else}
  <RepositoryViewMount {view} directory={route.directory} {runtimeRevision} />
{/if}
