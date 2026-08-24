<script lang="ts">
  let {
    label,
    percent,
    resetsAt,
    showReset,
    separator,
    column,
  }: {
    label: string;
    percent: number | null;
    resetsAt: number | null;
    showReset: boolean;
    separator: boolean;
    column: number;
  } = $props();

  const safePercent = $derived(
    percent === null ? 0 : Math.max(0, Math.min(100, percent)),
  );

  function resetText(epochSeconds: number): {
    readonly prefix: string;
    readonly value: string;
  } {
    const remaining = epochSeconds - Date.now() / 1_000;
    if (remaining <= 0) return { prefix: 'resets ', value: 'now' };
    if (remaining < 60) return { prefix: 'resets in ', value: '<1m' };
    const days = Math.trunc(remaining / 86_400);
    const hours = Math.trunc((remaining % 86_400) / 3_600);
    const minutes = Math.trunc((remaining % 3_600) / 60);
    const parts =
      days > 0
        ? [`${String(days)}d`, `${String(hours)}h`]
        : hours > 0
          ? [`${String(hours)}h`, `${String(minutes)}m`]
          : [`${String(minutes)}m`];
    return { prefix: 'resets in ', value: parts.join(' ') };
  }
</script>

<span
  class={[
    'ubar',
    {
      ghost: percent === null,
      hot: percent !== null && percent >= 90,
      warn: percent !== null && percent >= 70 && percent < 90,
      usep: separator,
    },
  ]}
  style:grid-column={column}
>
  <span class="ulabel">{label}</span>
  <span class="utrack">
    <span class="ufill" style:width={`${String(safePercent)}%`}></span>
  </span>
  <span class="upct">{percent === null ? '—' : `${String(percent)}%`}</span>
  {#if showReset}
    <span class="ureset">
      {#if percent !== null && resetsAt !== null}
        {@const reset = resetText(resetsAt)}
        <span class="rlbl">{reset.prefix}</span>
        <span class="rval">{reset.value}</span>
      {/if}
    </span>
  {/if}
</span>
