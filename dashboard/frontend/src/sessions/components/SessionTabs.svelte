<script lang="ts">
  import { formatRoute, SESSION_TABS } from '../../app/route';
  import type { SessionRoute, SessionTab } from '../../app/route';
  import type { SessionViewState } from '../session-view-state.svelte';
  import { webViewCatalog } from '../../extensions/views/web-view-catalog.svelte';

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

  const catalog = webViewCatalog();
  const extensionTabs = $derived(
    catalog?.forSlot('session_tab', 'session') ?? [],
  );

  function viewHref(extensionId: string, viewId: string): string {
    return formatRoute({
      kind: 'session',
      sessionId: route.sessionId,
      tab: 'mirror',
      ...(route.actorId === undefined ? {} : { actorId: route.actorId }),
      extensionView: { extensionId, viewId },
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
    <a
      class:on={route.extensionView === undefined && route.tab === tab}
      href={href(tab)}
    >
      {tab}
      {#if count(tab) > 0}<span class="count">{count(tab)}</span>{/if}
    </a>
  {/each}
  {#each extensionTabs as extensionTab (`${extensionTab.extension_id}:${extensionTab.view_id}`)}
    <a
      class:on={route.extensionView?.extensionId ===
        extensionTab.extension_id &&
        route.extensionView.viewId === extensionTab.view_id}
      href={viewHref(extensionTab.extension_id, extensionTab.view_id)}
    >
      {extensionTab.title}
    </a>
  {/each}
  {#if route.actorId !== undefined && route.tab === 'errors'}
    <span class="tabnote">session-wide</span>
  {/if}
</nav>
