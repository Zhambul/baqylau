<script lang="ts">
  import { compactNumber } from '../../shared/format';

  let {
    used,
    window,
    compacting,
  }: { used: number; window: number; compacting: boolean } = $props();

  const percent = $derived(window <= 0 ? 0 : Math.round((used * 100) / window));
  const boundedPercent = $derived(Math.max(0, Math.min(100, percent)));
  const stateClass = $derived(
    percent >= 90 ? 'hot' : percent >= 70 ? 'warn' : '',
  );
</script>

<div class={['cbar', stateClass, { compacting }]}>
  <span class="clabel">
    {#if compacting}<span class="cspin">⟳</span>
    {/if}ctx
  </span>
  <span class="ctrack">
    <span class="cfill" style:width={`${String(boundedPercent)}%`}></span>
  </span>
  <span class="cpct">{percent}%</span>
  <span class="cdetail">
    {compacting
      ? 'compacting…'
      : `${compactNumber(used)} / ${compactNumber(window)}`}
  </span>
</div>
