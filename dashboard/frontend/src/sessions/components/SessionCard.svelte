<script lang="ts">
  import { onDestroy } from 'svelte';

  import { getAppState } from '../../app/app-context';
  import { formatRoute } from '../../app/route';
  import { compactNumber, dollars, timeAgo } from '../../shared/format';
  import {
    STATUS_LABELS,
    sessionCost,
    sessionStatus,
    sessionTokenUsage,
    tokenCount,
  } from '../derived';
  import { lastActiveAt, leadActor } from '../model';
  import type { SessionSnapshot } from '../model';
  import ContextBar from './ContextBar.svelte';

  const CONFIRM_MILLISECONDS = 4_000;

  let { snapshot }: { snapshot: SessionSnapshot } = $props();

  const appState = getAppState();
  let armed = $state(false);
  let confirmTimer: ReturnType<typeof setTimeout> | null = null;

  const lead = $derived(leadActor(snapshot));
  const status = $derived(sessionStatus(snapshot));
  const statusLabel = $derived(
    status === null ? 'no tab' : (STATUS_LABELS.get(status) ?? status),
  );
  const tokens = $derived(sessionTokenUsage(snapshot));
  const totalTokens = $derived(tokenCount(tokens));
  const cost = $derived(sessionCost(snapshot));
  const sessionTitle = $derived(
    snapshot.session.title ??
      snapshot.session.workingDirectory.split('/').filter(Boolean).at(-1) ??
      snapshot.session.sessionId.slice(0, 18),
  );
  const closing = $derived(
    appState.closingSessions.has(snapshot.session.sessionId),
  );

  onDestroy(() => {
    if (confirmTimer !== null) clearTimeout(confirmTimer);
  });

  function requestClose(event: MouseEvent): void {
    event.preventDefault();
    event.stopPropagation();
    if (!armed) {
      armed = true;
      if (confirmTimer !== null) clearTimeout(confirmTimer);
      confirmTimer = setTimeout(() => {
        armed = false;
        confirmTimer = null;
      }, CONFIRM_MILLISECONDS);
      return;
    }
    armed = false;
    if (confirmTimer !== null) clearTimeout(confirmTimer);
    confirmTimer = null;
    void appState.requestSessionClose(snapshot.session.sessionId);
  }
</script>

<a
  class:closing
  class="scard"
  data-tab={status ?? ''}
  href={formatRoute({
    kind: 'session',
    sessionId: snapshot.session.sessionId,
    tab: 'mirror',
  })}
>
  <div class="proj">{sessionTitle}</div>
  <div class="sessionId">{snapshot.session.sessionId}</div>
  {#if !snapshot.live}
    <div class="corner">
      <span class="chip2 parked">
        {snapshot.session.state === 'finished' ? 'parked' : 'gone'}
      </span>
    </div>
  {:else if closing}
    <div class="corner">
      <span class="chip2 closing">closing…</span>
    </div>
  {:else}
    <div class="corner">
      <button
        class:arm={armed}
        class="xclose"
        type="button"
        title="close this session's terminal tab"
        onclick={requestClose}>{armed ? 'close?' : '✕'}</button
      >
    </div>
  {/if}
  <div class="row">
    <span class="badge" data-tab={status ?? ''}>
      <span class="st"></span>{statusLabel}
    </span>
    {#if (lead?.statistics.shellCommandCount ?? 0) > 0}
      <span
        ><span class="v">{lead?.statistics.shellCommandCount} cmds</span></span
      >
    {/if}
    {#if totalTokens > 0}
      <span><span class="v">{compactNumber(totalTokens)} tok</span></span>
    {/if}
    {#if cost !== null}
      <span><span class="cost">{dollars(cost)}</span></span>
    {/if}
    {#if lastActiveAt(snapshot) > 0}
      <span><span class="v">{timeAgo(lastActiveAt(snapshot))}</span></span>
    {/if}
    {#if snapshot.repository !== null}
      <span class="gitchip">
        <span class="gb"
          >⎇ {snapshot.repository.branch}{snapshot.repository.dirty
            ? '*'
            : ''}</span
        >
        {#if snapshot.repository.worktree !== null}
          <span class="gw">⋔ {snapshot.repository.worktree}</span>
        {/if}
      </span>
    {/if}
  </div>
  {#if (lead?.context.windowTokens ?? 0) > 0}
    <ContextBar
      used={lead?.context.usedTokens ?? 0}
      window={lead?.context.windowTokens ?? 0}
      compacting={lead?.context.compacting ?? false}
    />
  {/if}
</a>
