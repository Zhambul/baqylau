<script lang="ts">
  import { SvelteSet } from 'svelte/reactivity';

  import { getAppState } from '../../app/app-context';
  import { directoryName } from '../derived';
  import { groupSessions } from '../grouping';
  import SessionCard from './SessionCard.svelte';

  const FOLD_KINDS = ['parked', 'archived'] as const;
  const appState = getAppState();
  const openFolds = new SvelteSet<string>();
  const groups = $derived(
    groupSessions(appState.sessions, appState.hiddenDirectories),
  );

  function foldKey(directory: string, kind: 'parked' | 'archived'): string {
    return `${directory}|${kind}`;
  }

  function toggleFold(directory: string, kind: 'parked' | 'archived'): void {
    const key = foldKey(directory, kind);
    if (openFolds.has(key)) {
      openFolds.delete(key);
    } else {
      openFolds.add(key);
    }
  }

  function foldOpen(directory: string, kind: 'parked' | 'archived'): boolean {
    return openFolds.has(foldKey(directory, kind));
  }

  function sessionCount(count: number): string {
    return `${String(count)} ${count === 1 ? 'session' : 'sessions'}`;
  }
</script>

{#if appState.listState === 'loading'}
  <div class="waiting">loading sessions…</div>
{:else if appState.listState === 'failed' && appState.sessions.length === 0}
  <div class="empty">sessions could not be loaded</div>
{:else if appState.sessions.length === 0}
  <div class="empty">no sessions recorded yet</div>
{:else}
  {#each groups as group (group.projectDirectory)}
    <div class="dirhead">
      <span class="dirname"
        >{directoryName(group.projectDirectory) || 'no project'}</span
      >
      {#if group.projectDirectory.length > 0}
        <span class="dirpath">{group.projectDirectory}</span>
      {/if}
      <span class="dircount">{sessionCount(group.count)}</span>
      {#if group.projectDirectory.length > 0}
        <button
          class="dirnew"
          type="button"
          title={`new session in ${group.projectDirectory}`}
          onclick={() => {
            appState.requestNewSession(group.projectDirectory);
          }}>+</button
        >
      {/if}
      <button
        class="dirhide"
        type="button"
        disabled={group.active.length > 0}
        title={group.active.length > 0
          ? `can't hide — ${String(group.active.length)} ${group.active.length === 1 ? 'active session' : 'active sessions'} here`
          : group.projectDirectory.length > 0
            ? 'hide this directory from the list (re-appears when a new session starts here)'
            : 'hide the projectless sessions from the list (re-appears when a new one starts)'}
        onclick={() => {
          void appState.hideWorkingDirectory(group.projectDirectory);
        }}>✕</button
      >
    </div>
    {#if group.active.length > 0}
      <div class="sgrid">
        {#each group.active as snapshot (snapshot.session.sessionId)}
          <SessionCard {snapshot} />
        {/each}
      </div>
    {/if}
    {#each FOLD_KINDS as kind (kind)}
      {@const rows = group[kind]}
      {#if rows.length > 0}
        <button
          class:open={foldOpen(group.projectDirectory, kind)}
          class="fold"
          type="button"
          onclick={() => {
            toggleFold(group.projectDirectory, kind);
          }}
        >
          {foldOpen(group.projectDirectory, kind) ? '▾' : '▸'}
          {kind} · {rows.length}
        </button>
        {#if foldOpen(group.projectDirectory, kind)}
          <div class="sgrid folded">
            {#each rows as snapshot (snapshot.session.sessionId)}
              <SessionCard {snapshot} />
            {/each}
          </div>
        {/if}
      {/if}
    {/each}
  {/each}
{/if}
