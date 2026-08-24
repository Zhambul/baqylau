<script lang="ts">
  import { onMount, untrack } from 'svelte';

  import { getAppState } from '../../app/app-context';
  import type { SessionRoute } from '../../app/route';
  import { SessionViewState } from '../session-view-state.svelte';
  import AgentCard from './AgentCard.svelte';
  import AgentsView from './AgentsView.svelte';
  import AskCard from './AskCard.svelte';
  import Composer from './Composer.svelte';
  import ErrorsView from './ErrorsView.svelte';
  import FeedView from './FeedView.svelte';
  import GoalTasks from './GoalTasks.svelte';
  import PlanCard from './PlanCard.svelte';
  import SessionHeader from './SessionHeader.svelte';
  import ShellSectionView from './ShellSectionView.svelte';
  import SessionTabs from './SessionTabs.svelte';

  let { route }: { route: SessionRoute } = $props();

  const appState = getAppState();
  const initialRoute = untrack(() => route);
  const view = new SessionViewState(
    initialRoute.sessionId,
    initialRoute.actorId,
    appState,
  );

  onMount(() => {
    const controller = new AbortController();
    const unregister = appState.registerSession(view);
    void view.initialize(controller.signal);
    return () => {
      controller.abort();
      unregister();
      view.destroy();
    };
  });
</script>

{#if view.session === null}
  {#if view.loadState === 'failed'}
    <div class="empty">session could not be loaded</div>
  {:else}
    <div class="waiting">loading session…</div>
  {/if}
{:else}
  <SessionHeader {view} />
  <SessionTabs {route} {view} />

  {#if route.tab === 'mirror'}
    {#if route.actorId === undefined}
      <GoalTasks session={view.session} />
      {#if view.attention.plan !== null}
        <PlanCard entry={view.attention.plan} {view} />
      {/if}
      {#if view.attention.question !== null}
        <AskCard entry={view.attention.question} {view} />
      {/if}
      <Composer {view} />
    {/if}
    <div class="fbar">
      <div class="vmodes" aria-label="feed density">
        {#each ['verbose', 'default', 'focus'] as mode (mode)}
          <button
            class:on={view.application?.preferences.viewMode === mode}
            class="vmode"
            type="button"
            onclick={() =>
              view.setViewMode(
                mode === 'verbose'
                  ? 'verbose'
                  : mode === 'focus'
                    ? 'focus'
                    : 'default',
              )}
          >
            {mode}
          </button>
        {/each}
      </div>
      <span class="fcount"
        >{view.visibleFeedCount} of {view.feedItems.length} shown</span
      >
    </div>
    <div class="split">
      <div class="scol"><FeedView {view} /></div>
      <aside class="rail" aria-label="agents">
        {#if view.childActors.length > 0}<div class="mhead">agents</div>{/if}
        {#each view.childActors as actor (actor.actorId)}
          <AgentCard {actor} />
        {/each}
      </aside>
    </div>
  {:else if route.tab === 'agents'}
    <AgentsView {view} />
  {:else if route.tab === 'errors'}
    {#if view.applicationState === 'loading'}
      <div class="waiting">loading errors…</div>
    {:else}
      <ErrorsView errors={view.application?.errors ?? []} />
    {/if}
  {:else if route.tab === 'monitors'}
    <ShellSectionView kind="monitor" detail={route.detail} {view} />
  {:else}
    <ShellSectionView kind="job" detail={route.detail} {view} />
  {/if}
{/if}
