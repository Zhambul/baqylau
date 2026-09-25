<script lang="ts">
  import type { ExtensionScope } from '@baqylau/extension-api';

  import type { WebView } from '../../api/extension-views';
  import ExtensionViewMount from './ExtensionViewMount.svelte';
  import { webViewCatalog } from './web-view-catalog.svelte';

  let {
    slot,
    scope,
    label,
  }: {
    slot: WebView['slot'];
    scope: ExtensionScope;
    label: string;
  } = $props();

  const catalog = webViewCatalog();
  const views = $derived(catalog?.forSlot(slot, scope.kind) ?? []);
  const runtimeRevision = $derived(catalog?.runtimeRevision ?? null);
</script>

{#if runtimeRevision !== null && views.length > 0}
  <div class="extension-slot" data-slot={slot} aria-label={label}>
    {#each views as view (`${runtimeRevision}:${view.extension_id}:${view.view_id}`)}
      <ExtensionViewMount {view} {scope} {runtimeRevision} />
    {/each}
  </div>
{/if}

<style>
  .extension-slot {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    align-items: center;
    min-width: 0;
  }

  .extension-slot[data-slot='feed'] {
    flex-direction: column;
    align-items: stretch;
  }
</style>
