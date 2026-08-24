<script lang="ts">
  import type { ToastNotice } from '../../app/app-state.svelte';

  let {
    notices,
    ondismiss,
  }: {
    notices: readonly ToastNotice[];
    ondismiss: (id: number, action: (() => void) | null) => void;
  } = $props();
</script>

<div id="toasts" aria-live="polite">
  {#each notices as notice (notice.id)}
    <button
      class={['toast', notice.kind]}
      type="button"
      onclick={() => {
        ondismiss(notice.id, notice.action);
      }}
    >
      <span class="t1">{notice.heading}</span>
      {#if notice.detail.length > 0}
        <span class="t2">{notice.detail}</span>
      {/if}
    </button>
  {/each}
</div>

<style>
  .toast {
    display: block;
    border-top: 0;
    border-right: 0;
    border-bottom: 0;
    color: inherit;
    font: inherit;
    text-align: left;
  }

  .t1,
  .t2 {
    display: block;
  }
</style>
