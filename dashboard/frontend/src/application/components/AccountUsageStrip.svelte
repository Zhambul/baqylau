<script lang="ts">
  import { getAppState } from '../../app/app-context';
  import type { UsageRow } from '../model';
  import {
    accountName,
    usageColumns,
    usageTracks,
    windowsBySlot,
  } from '../usage-layout';
  import UsageBar from './UsageBar.svelte';

  const MINIMUM_NAME_CHARACTERS = 14;
  const appState = getAppState();
  const shown = $derived(
    (appState.application?.usageRows ?? []).filter(
      (row) => row.windows.length > 0 || row.authenticationError !== null,
    ),
  );
  const columns = $derived(usageColumns(shown));
  const hasAuthenticationError = $derived(
    shown.some((row) => row.authenticationError !== null),
  );
  const tracks = $derived(usageTracks(columns.length, hasAuthenticationError));
  const nameCharacters = $derived(
    shown.reduce(
      (width, row) => Math.max(width, accountName(row).length),
      MINIMUM_NAME_CHARACTERS,
    ),
  );
  const gridStyle = $derived(
    `--aname-w: ${String(nameCharacters)}ch; --acct-tracks: ${new Array(
      tracks.count,
    )
      .fill('max-content')
      .join(' ')}`,
  );
  const harnesses = $derived([...new Set(shown.map((row) => row.harness))]);

  function percent(row: UsageRow, slot: string, index: number): number | null {
    const value = windowsBySlot(row).get(slot)?.[index]?.usedPercent;
    if (value === undefined) return null;
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }

  function limitLabel(row: UsageRow): string {
    return `${row.limit?.modelId === null ? '' : `${row.limit?.modelId ?? ''} `}limit hit`;
  }

  function resetAgo(epochSeconds: number): string {
    const remaining = epochSeconds - Date.now() / 1_000;
    if (remaining <= 0) return 'now';
    if (remaining < 60) return 'in <1m';
    const days = Math.trunc(remaining / 86_400);
    const hours = Math.trunc((remaining % 86_400) / 3_600);
    const minutes = Math.trunc((remaining % 3_600) / 60);
    return `in ${(days > 0
      ? [`${String(days)}d`, `${String(hours)}h`]
      : hours > 0
        ? [`${String(hours)}h`, `${String(minutes)}m`]
        : [`${String(minutes)}m`]
    ).join(' ')}`;
  }
</script>

<div id="accounts" hidden={shown.length === 0} style={gridStyle}>
  {#each harnesses as harness (harness)}
    {#each shown.filter((row) => row.harness === harness) as row (`${row.harness}:${row.accountId ?? ''}`)}
      {@const slots = windowsBySlot(row)}
      <div class="acct" style:grid-column="1 / -1">
        <span class="aname" style:grid-column={tracks.name}
          >{accountName(row)}</span
        >
        {#if hasAuthenticationError}
          <span
            class:ghost={row.authenticationError === null}
            class="uauth"
            style:grid-column={tracks.badge}
            title={row.authenticationError ?? undefined}
            aria-hidden={row.authenticationError === null}>⚠ logged out</span
          >
        {/if}
        {#if row.windows.length === 0}
          {#if row.authenticationError === null}
            <span
              class="adim"
              style:grid-column={`${String(tracks.firstBar)} / -1`}
              >no usage yet</span
            >
          {/if}
        {:else}
          {#each columns as column, index (`${column.slot}:${String(column.index)}`)}
            {@const current = slots.get(column.slot)?.[column.index]}
            {#if current !== undefined || column.hosts.has(row.harness)}
              <UsageBar
                label={column.label}
                percent={percent(row, column.slot, column.index)}
                resetsAt={current?.resetsAt ?? null}
                showReset={column.scope === 'account'}
                separator={index > 0}
                column={tracks.firstBar + index}
              />
            {/if}
          {/each}
        {/if}
        {#if row.limit !== null}
          <span class="utail" style:grid-column={tracks.tail}>
            <span class="ulimit" title={row.limit.message ?? undefined}
              >{limitLabel(row)}</span
            >
            {#if row.limit.resetsAt !== null}
              <span class="ureset">resets {resetAgo(row.limit.resetsAt)}</span>
            {/if}
          </span>
        {/if}
      </div>
    {/each}
  {/each}
</div>
