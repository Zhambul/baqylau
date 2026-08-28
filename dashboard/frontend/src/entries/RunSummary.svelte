<script lang="ts">
  import { onDestroy } from 'svelte';

  import { duration } from '../shared/format';
  import type { RunSummary } from './feed-model';

  let { summary, ontoggle }: { summary: RunSummary; ontoggle: () => void } =
    $props();

  let now = $state(Date.now() / 1_000);
  let timer: ReturnType<typeof setInterval> | null = null;

  $effect(() => {
    if (summary.running && summary.anchor > 0) {
      timer ??= setInterval(() => {
        now = Date.now() / 1_000;
      }, 1_000);
    } else if (timer !== null) {
      clearInterval(timer);
      timer = null;
    }
  });

  onDestroy(() => {
    if (timer !== null) clearInterval(timer);
  });
</script>

<button
  class="vsum"
  class:open={summary.open}
  data-open={summary.open ? '1' : '0'}
  type="button"
  onclick={ontoggle}
>
  <span
    class:bad={summary.bad && !summary.running}
    class:done={!summary.bad && !summary.running}
    class="vdot"
  ></span>
  <span class="vtext">
    {#each summary.fragments as fragment, index (`${fragment.verb}:${String(fragment.count)}`)}
      {#if index > 0},
      {/if}{fragment.verb} <b>{fragment.count}</b>
      {fragment.count === 1
        ? fragment.singular
        : fragment.plural}{#if index === 0 && (summary.linesAdded > 0 || summary.linesRemoved > 0)}
        {#if summary.linesAdded > 0}<span class="dadd"
            >+{summary.linesAdded}</span
          >{/if}
        {#if summary.linesRemoved > 0}<span class="drem"
            >−{summary.linesRemoved}</span
          >{/if}
      {/if}
    {/each}{#if summary.running}…{/if}
  </span>
  {#if summary.running && now - summary.anchor >= 2}
    <span class="vtimer">· {duration(now - summary.anchor)}</span>
  {/if}
  <span class="vcaret">{summary.open ? '▾' : '▸'}</span>
</button>

<style>
  button.vsum {
    width: 100%;
    border: 0;
    background: transparent;
    text-align: left;
  }

  button.vsum[data-open='1'] {
    background: var(--panel);
  }

  .dadd,
  .drem {
    margin-left: 5px;
  }
</style>
