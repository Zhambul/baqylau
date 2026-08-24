<script lang="ts">
  import { formatRoute, SESSION_TABS } from '../../app/route';
  import type { SessionRoute, SessionTab } from '../../app/route';
  import type { SessionViewState } from '../session-view-state.svelte';

  let { route, view }: { route: SessionRoute; view: SessionViewState } =
    $props();

  function href(tab: SessionTab): string {
    return formatRoute({
      kind: 'session',
      sessionId: route.sessionId,
      tab,
      ...(route.actorId === undefined || tab === 'agents'
        ? {}
        : { actorId: route.actorId }),
    });
  }

  function count(tab: SessionTab): number {
    const actor = view.scopedActor;
    switch (tab) {
      case 'agents':
        return view.childActors.length;
      case 'monitors':
        return view.monitors.length > 0
          ? view.monitors.length
          : (actor?.background.monitorCount ?? 0);
      case 'jobs':
        return view.jobs.length > 0
          ? view.jobs.length
          : (actor?.background.backgroundJobCount ?? 0);
      case 'errors':
        return view.application?.errors.length ?? 0;
      case 'mirror':
        return 0;
    }
  }
</script>

<nav class="tabs" aria-label="session views">
  {#each SESSION_TABS as tab (tab)}
    <a class:on={route.tab === tab} href={href(tab)}>
      {tab}
      {#if count(tab) > 0}<span class="count">{count(tab)}</span>{/if}
    </a>
  {/each}
  {#if route.actorId !== undefined && route.tab === 'errors'}
    <span class="tabnote">session-wide</span>
  {/if}
</nav>
