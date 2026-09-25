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
  import ExtensionSlot from '../../extensions/views/ExtensionSlot.svelte';
  import ExtensionViewMount from '../../extensions/views/ExtensionViewMount.svelte';
  import WorkspaceLinks from '../../extensions/views/WorkspaceLinks.svelte';
  import { webViewCatalog } from '../../extensions/views/web-view-catalog.svelte';

  let { route }: { route: SessionRoute } = $props();

  const appState = getAppState();
  const initialRoute = untrack(() => route);
  const view = new SessionViewState(
    initialRoute.sessionId,
    initialRoute.actorId,
    appState,
  );

  const catalog = webViewCatalog();
  const sessionScope = $derived.by(() => {
    const session = view.session;
    const actorId = view.scopedActorId;
    if (session === null || actorId === null) return null;
    return {
      kind: 'session',
      session_id: session.sessionId,
      actor_id: actorId,
      harness: session.harness,
    } as const;
  });
  const extensionMount = $derived.by(() => {
    const reference = route.extensionView;
    const scope = sessionScope;
    const runtimeRevision = catalog?.runtimeRevision ?? null;
    const selected =
      reference === undefined ? null : (catalog?.find(reference) ?? null);
    if (selected === null || scope === null || runtimeRevision === null)
      return null;
    return {
      key: `${runtimeRevision}:${selected.extension_id}:${selected.view_id}:${scope.actor_id}`,
      view: selected,
      runtimeRevision,
      scope,
    };
  });

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
  {#if sessionScope !== null}
    <WorkspaceLinks
      scope={sessionScope}
      directory={view.session.workingDirectory}
    />
  {/if}
  <SessionTabs {route} {view} />

  {#if route.extensionView !== undefined}
    {#if extensionMount === null}
      <div class="empty" role="status">
        This extension view is not available.
      </div>
    {:else}
      {#key extensionMount.key}
        <ExtensionViewMount
          view={extensionMount.view}
          scope={extensionMount.scope}
          runtimeRevision={extensionMount.runtimeRevision}
        />
      {/key}
    {/if}
  {:else if route.tab === 'mirror'}
    {#if route.actorId === undefined}
      <GoalTasks
        session={view.session}
        hidden={view.application?.preferences.goalHidden ?? false}
      />
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
    {#if sessionScope !== null}
      <ExtensionSlot slot="feed" scope={sessionScope} label="feed additions" />
    {/if}
    <div class="split">
      <div class="scol"><FeedView {view} scope={sessionScope} /></div>
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
