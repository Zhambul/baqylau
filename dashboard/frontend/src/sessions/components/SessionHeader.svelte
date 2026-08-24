<script lang="ts">
  import { formatRoute } from '../../app/route';
  import {
    compactNumber,
    dollars,
    duration,
    timeAgo,
  } from '../../shared/format';
  import {
    actorDisplayName,
    actorEventCount,
    actorGlyph,
    actorState,
  } from '../agent-presentation';
  import { STATUS_LABELS, directoryName } from '../derived';
  import type { Actor, TokenUsage } from '../model';
  import type { SessionViewState } from '../session-view-state.svelte';
  import { runningSlots } from '../shell-fold';
  import ContextBar from './ContextBar.svelte';

  let { view }: { view: SessionViewState } = $props();

  const actor = $derived(view.scopedActor);
  const scopedState = $derived(
    view.actorId === undefined || actor === null ? null : actorState(actor),
  );
  const title = $derived(
    view.actorId === undefined
      ? (view.session?.title ??
          (directoryName(view.session?.workingDirectory ?? '') ||
            shortSessionId(view.sessionId)))
      : actor === null
        ? view.actorId
        : `${actorGlyph(actor)} ${actorDisplayName(actor)}`,
  );
  const status = $derived(actor?.status ?? null);
  const statusLabel = $derived(
    scopedState?.label ??
      (status === null ? 'no tab' : (STATUS_LABELS.get(status) ?? status)),
  );
  const tokens = $derived(totalTokens(actor));
  const cost = $derived(actorCost(actor));
  const eventCount = $derived(actor === null ? 0 : actorEventCount(actor));
  const actorAge = $derived(
    actor?.startedAt === null || actor === null
      ? ''
      : actor.finishedAt === null
        ? timeAgo(actor.startedAt)
        : duration(actor.finishedAt - actor.startedAt),
  );
  const liveSlots = $derived(runningSlots(view.shellFolds));

  function shortSessionId(value: string): string {
    return value.length > 20
      ? `${value.slice(0, 8)}…${value.slice(-4)}`
      : value;
  }

  function totalTokens(value: Actor | null): number {
    if (value === null) return 0;
    const usage: TokenUsage = value.usage.tokens;
    return (
      usage.inputTokens +
      usage.outputTokens +
      usage.cacheReadTokens +
      usage.cacheWriteTokens +
      usage.oneHourCacheWriteTokens
    );
  }

  function actorCost(value: Actor | null): number | null {
    if (value?.usage.costInUsd === null || value === null) return null;
    const parsed = Number(value.usage.costInUsd);
    return Number.isFinite(parsed) ? parsed : null;
  }

  async function copySessionId(): Promise<void> {
    await navigator.clipboard.writeText(view.sessionId);
  }
</script>

<div
  class="shead"
  data-tab={scopedState === null ? (status ?? '') : undefined}
  data-st={scopedState?.className}
>
  <div class="l1">
    <span class="proj">{title}</span>
    <span
      class="badge"
      data-tab={scopedState === null ? (status ?? '') : undefined}
      data-st={scopedState?.className}
    >
      <span class="st"></span>{statusLabel}
    </span>
    {#if view.live === false}
      <span class="chip2 parked">parked</span>
    {/if}
    {#if view.session?.workingDirectory}
      <span class="sessionId" title={view.session.workingDirectory}>
        {directoryName(view.session.workingDirectory)}
      </span>
    {/if}
    <button
      class="sessionId copysid"
      type="button"
      title="click to copy the full session id"
      onclick={copySessionId}
    >
      {shortSessionId(view.sessionId)}
    </button>
    {#if view.repository !== null}
      <span class="gitchip">
        <span class="gb"
          >⎇ {view.repository.branch}{view.repository.dirty ? '*' : ''}</span
        >
        {#if view.repository.worktree !== null}
          <span class="gw">⋔ {view.repository.worktree}</span>
        {/if}
      </span>
    {/if}
    {#if view.session?.account !== null && view.session?.account !== undefined}
      <span class="acctchip">
        <span class="ag">◈</span>
        {view.session.account.accountId} · {view.session.account.displayName}
      </span>
    {/if}
  </div>
  {#if actor !== null}
    <div class="statsrow">
      {#if view.actorId !== undefined}
        <a
          class="backses"
          href={formatRoute({
            kind: 'session',
            sessionId: view.sessionId,
            tab: 'mirror',
          })}>← session</a
        >
        {#if scopedState !== null}
          <span class={scopedState.className}>{scopedState.label}</span>
        {/if}
        {#if actor.model !== null}
          <span class="amodel"
            >{actor.model}{actor.effort === null
              ? ''
              : `·${actor.effort}`}</span
          >
        {/if}
        <span>{eventCount} events</span>
        {#if actorAge.length > 0}<span>⏱ {actorAge}</span>{/if}
      {:else}
        {#if actor.statistics.shellCommandCount > 0}
          <span
            ><span class="v">{actor.statistics.shellCommandCount}</span>
            cmds</span
          >
        {/if}
        {#if actor.statistics.failedShellCommandCount > 0}
          <span class="neg">({actor.statistics.failedShellCommandCount}✗)</span>
        {/if}
        {#if actor.statistics.fileCount > 0}
          <span><span class="v">{actor.statistics.fileCount}</span> files</span>
        {/if}
        {#if actor.statistics.linesAdded > 0}
          <span class="pos">+{actor.statistics.linesAdded}</span>
        {/if}
        {#if actor.statistics.linesRemoved > 0}
          <span class="neg">−{actor.statistics.linesRemoved}</span>
        {/if}
      {/if}
      {#if tokens > 0}
        <span><span class="v">Σ {compactNumber(tokens)}</span> tok</span>
      {/if}
      {#if cost !== null}
        <span class="cost">{dollars(cost)}</span>
      {/if}
      {#if view.actorId === undefined && actor.model !== null}
        <span class="amodel">{actor.model}</span>
      {/if}
      {#if view.actorId === undefined && actor.effort !== null}
        <span class="amodel">{actor.effort}</span>
      {/if}
    </div>
    {#if actor.context.windowTokens > 0}
      <div class="ctxrow">
        <ContextBar
          used={actor.context.usedTokens}
          window={actor.context.windowTokens}
          compacting={view.actorId === undefined && actor.context.compacting}
        />
      </div>
    {/if}
  {/if}
  {#if view.actorId === undefined && liveSlots.length > 0}
    <div class="runrow">
      {#each liveSlots as slot (slot.key)}
        <span class={`rchip rk-${slot.kind}`}
          ><span class="rg">{slot.glyph}</span> {slot.label}</span
        >
      {/each}
    </div>
  {/if}
</div>

<style>
  button.sessionId {
    border: 0;
    background: transparent;
    padding: 0;
  }
</style>
