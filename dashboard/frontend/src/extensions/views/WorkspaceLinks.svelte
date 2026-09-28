<script lang="ts">
  import type { ExtensionScope } from '@baqylau/extension-api';

  import { readRelatedScopes } from '../../api/extension-views';
  import { formatRoute } from '../../app/route';
  import { webViewCatalog } from './web-view-catalog.svelte';

  let { scope, directory }: { scope: ExtensionScope; directory: string } =
    $props();

  const catalog = webViewCatalog();
  const pages = $derived(catalog?.forSlot('workspace_page', 'workspace') ?? []);
  // A repository page opens with the repository of the session's directory;
  // the page asks the daemon for that repository.
  const repositoryPages = $derived(
    catalog?.forSlot('workspace_page', 'repository') ?? [],
  );
  const scopeText = $derived(JSON.stringify(scope));
  let workspaceIds = $state<readonly string[]>([]);

  // The daemon names the related workspace, so that the browser does not
  // repeat how a workspace is identified.
  $effect(() => {
    const controller = new AbortController();
    void readRelatedScopes(scopeText, controller.signal)
      .then((related) => {
        workspaceIds = related.flatMap((entry) =>
          entry.kind === 'workspace' ? [entry.workspace_id] : [],
        );
      })
      .catch(() => {
        if (!controller.signal.aborted) workspaceIds = [];
      });
    return () => {
      controller.abort();
    };
  });
</script>

{#if (pages.length > 0 && workspaceIds.length > 0) || repositoryPages.length > 0}
  <nav class="workspace-links" aria-label="workspace pages">
    {#each repositoryPages as page (`${page.extension_id}:${page.view_id}`)}
      <a
        href={formatRoute({
          kind: 'repository-page',
          directory,
          view: { extensionId: page.extension_id, viewId: page.view_id },
        })}
      >
        {page.title}
      </a>
    {/each}
    {#each workspaceIds as workspaceId (workspaceId)}
      {#each pages as page (`${page.extension_id}:${page.view_id}`)}
        <a
          href={formatRoute({
            kind: 'extension-page',
            workspaceId,
            view: { extensionId: page.extension_id, viewId: page.view_id },
          })}
        >
          {page.title}
        </a>
      {/each}
    {/each}
  </nav>
{/if}

<style>
  .workspace-links {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    padding: 2px 0 6px;
    font-size: 12px;
  }
</style>
