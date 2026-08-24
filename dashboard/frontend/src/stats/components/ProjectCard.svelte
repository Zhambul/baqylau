<script lang="ts">
  import { compactNumber, dollars } from '../../shared/format';
  import { sparklinePoints } from '../charts';
  import type { ProjectInsights } from '../model';

  let { project }: { project: ProjectInsights } = $props();
  const points = $derived(sparklinePoints(project.dailySessions));
</script>

<div class="projcard">
  <div class="pchead">
    <span class="pcname">{project.name}</span>
    <span class="pcses">{project.sessionCount} sess</span>
  </div>
  <svg class="spark" viewBox="0 0 220 34" preserveAspectRatio="none">
    {#if points.length > 0}
      <polyline {points} class="sparkline" fill="none"></polyline>
    {/if}
  </svg>
  <div class="statsrow">
    <span class="kv"
      ><span class="k">Σ</span><span class="v gold"
        >{compactNumber(project.tokenCount)}</span
      ></span
    >
    <span class="kv"
      ><span class="k">$</span><span class="v cost"
        >{dollars(project.costInUsd)}</span
      ></span
    >
    {#if project.errorCount > 0}
      <span class="kv"
        ><span class="k">⚠</span><span class="v neg">{project.errorCount}</span
        ></span
      >
    {/if}
  </div>
</div>
