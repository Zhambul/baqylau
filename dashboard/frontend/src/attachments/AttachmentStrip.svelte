<script lang="ts">
  import type { AttachmentTrayState } from './attachment-tray.svelte';

  let { tray }: { tray: AttachmentTrayState } = $props();
</script>

<div class:has={tray.items.length > 0} class="attach-strip">
  {#each tray.items as item (item.id)}
    <div
      class:pending={item.state === 'pending'}
      class:failed={item.state === 'failed'}
      class="attach-chip"
    >
      {#if item.isImage && item.thumbnailUrl.length > 0}
        <img class="attach-thumb" src={item.thumbnailUrl} alt="" />
      {:else}
        <span class="attach-icon">▤</span>
      {/if}
      <span class="attach-name">{item.name}</span>
      <button
        class="attach-x"
        type="button"
        title="remove attachment"
        onclick={() => {
          tray.remove(item.id);
        }}>✕</button
      >
    </div>
  {/each}
</div>
