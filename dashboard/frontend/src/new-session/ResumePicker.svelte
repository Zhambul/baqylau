<script lang="ts">
  import { onDestroy, tick } from 'svelte';
  import { SvelteMap } from 'svelte/reactivity';

  import { readEntryPage } from '../api/entries';
  import { readResumableSessions } from '../api/new-session';
  import { readSession } from '../api/session-data';
  import type { ActorId, SessionId } from '../app/domain-ids';
  import FeedItem from '../entries/FeedItem.svelte';
  import { buildFeedItems } from '../entries/feed-model';
  import type { FeedItem as FeedItemModel } from '../entries/feed-model';
  import type { HarnessDescription } from '../harnesses/model';
  import type { ResumableSession } from './model';
  import { initialEntriesNewestFirst } from '../sessions/session-reducer';
  import { foldShellEntries } from '../sessions/shell-fold';
  import { timeAgo } from '../shared/format';

  const SEARCH_DELAY_MS = 250;

  let {
    workingDirectory,
    harnesses,
    value = $bindable<SessionId | null>(null),
    onselect,
  }: {
    workingDirectory: string;
    harnesses: readonly HarnessDescription[];
    value?: SessionId | null;
    onselect: (session: ResumableSession) => void;
  } = $props();

  let search = $state('');
  let rows = $state<readonly ResumableSession[]>([]);
  let loading = $state(false);
  let failure = $state<string | null>(null);
  let activeIndex = $state(0);
  let searchTimer: ReturnType<typeof setTimeout> | null = null;
  let request: AbortController | null = null;
  let preview = $state<ResumableSession | null>(null);
  let previewItems = $state<readonly FeedItemModel[]>([]);
  let previewLoading = $state(false);
  let previewFailure = $state<string | null>(null);
  let resumeList = $state<HTMLElement>();
  let previewPanel = $state<HTMLElement>();
  let previewClose = $state<HTMLButtonElement>();
  let previewRequest: AbortController | null = null;
  let previewReturnFocus: HTMLElement | null = null;

  $effect(() => {
    const directory = workingDirectory;
    const query = search;
    schedule(directory, query);
  });

  onDestroy(() => {
    if (searchTimer !== null) clearTimeout(searchTimer);
    request?.abort();
    previewRequest?.abort();
  });

  function schedule(directory: string, query: string): void {
    if (searchTimer !== null) clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      searchTimer = null;
      void load(directory, query);
    }, SEARCH_DELAY_MS);
  }

  function retry(): void {
    if (searchTimer !== null) clearTimeout(searchTimer);
    searchTimer = null;
    void load(workingDirectory, search);
  }

  async function load(directory: string, query: string): Promise<void> {
    request?.abort();
    const controller = new AbortController();
    request = controller;
    loading = true;
    failure = null;
    try {
      const result = await readResumableSessions(
        directory.trim(),
        query.trim(),
        controller.signal,
      );
      if (request !== controller) return;
      rows = result.slice(0, 25);
      const selectedIndex = rows.findIndex((row) => row.sessionId === value);
      activeIndex = selectedIndex >= 0 ? selectedIndex : 0;
      if (value !== null && selectedIndex < 0) value = null;
    } catch (error) {
      if (controller.signal.aborted) return;
      failure = error instanceof Error ? error.message : String(error);
      rows = [];
      value = null;
    } finally {
      if (request === controller) {
        loading = false;
        request = null;
      }
    }
  }

  function choose(row: ResumableSession): void {
    value = row.sessionId;
    onselect(row);
  }

  function keydown(event: KeyboardEvent): void {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      if (rows.length === 0) return;
      const delta = event.key === 'ArrowDown' ? 1 : -1;
      activeIndex = (activeIndex + delta + rows.length) % rows.length;
      void scrollActiveRowIntoView();
      return;
    }
    const row = rows[activeIndex];
    if (row === undefined) return;
    if (event.key === 'Enter') {
      event.preventDefault();
      choose(row);
    } else if (event.key === ' ') {
      event.preventDefault();
      void openPreview(row);
    }
  }

  async function scrollActiveRowIntoView(): Promise<void> {
    await tick();
    const row = resumeList?.children.item(activeIndex);
    if (row instanceof HTMLElement)
      row.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  }

  async function openPreview(row: ResumableSession): Promise<void> {
    previewRequest?.abort();
    const controller = new AbortController();
    previewRequest = controller;
    if (preview === null)
      previewReturnFocus =
        document.activeElement instanceof HTMLElement
          ? document.activeElement
          : null;
    preview = row;
    previewItems = [];
    previewLoading = true;
    previewFailure = null;
    await tick();
    previewClose?.focus();
    try {
      const [snapshot, page] = await Promise.all([
        readSession(row.sessionId, controller.signal),
        readEntryPage(row.sessionId, { limit: 100, signal: controller.signal }),
      ]);
      if (
        controller.signal.aborted ||
        previewRequest !== controller ||
        !previewMatches(row.sessionId)
      )
        return;
      const actorNames = new SvelteMap<ActorId, string>();
      for (const actor of snapshot.actors)
        actorNames.set(actor.actorId, actor.name);
      const newest = initialEntriesNewestFirst(page.items);
      const lead = snapshot.actors.find(
        (actor) => actor.actorId === snapshot.session.leadActorId,
      );
      const shells = foldShellEntries(
        newest,
        lead?.background.runningShellIds ?? [],
      );
      const supportsReadableCompactionContext =
        harnesses.find((harness) => harness.name === snapshot.session.harness)
          ?.supportsReadableCompactionContext ?? false;
      previewItems = [
        ...buildFeedItems(
          newest,
          actorNames,
          shells,
          supportsReadableCompactionContext,
        ),
      ].reverse();
    } catch (error) {
      if (controller.signal.aborted) return;
      previewFailure = error instanceof Error ? error.message : String(error);
    } finally {
      if (previewRequest === controller) {
        previewLoading = false;
        previewRequest = null;
      }
    }
  }

  function previewMatches(sessionId: SessionId): boolean {
    return preview?.sessionId === sessionId;
  }

  function previewKeydown(event: KeyboardEvent): void {
    if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      closePreview();
      return;
    }
    if (event.key !== 'Tab' || previewPanel === undefined) return;
    const focusable = [
      ...previewPanel.querySelectorAll<HTMLElement>(
        'a[href], button:not(:disabled), input:not(:disabled), [tabindex="0"]',
      ),
    ].filter((element) => !element.hidden);
    const first = focusable[0];
    const last = focusable.at(-1);
    if (first === undefined || last === undefined) return;
    event.stopPropagation();
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  function closePreview(): void {
    const returnFocus = previewReturnFocus;
    previewRequest?.abort();
    previewRequest = null;
    preview = null;
    previewItems = [];
    previewLoading = false;
    previewFailure = null;
    previewReturnFocus = null;
    void tick().then(() => {
      if (returnFocus?.isConnected === true) returnFocus.focus();
    });
  }
</script>

<input
  bind:value={search}
  class="nsinput nsressearch"
  type="search"
  placeholder="search all sessions in this directory…"
  onkeydown={keydown}
/>
<div class="nsreshint">↑/↓ choose · Enter select · Space preview</div>
<div
  bind:this={resumeList}
  class="nsreslist"
  role="listbox"
  aria-label="sessions to resume"
>
  {#if loading}
    <div class="nsresempty">loading sessions…</div>
  {:else if failure !== null}
    <div class="nsresempty" role="alert">
      <span>could not load sessions</span>
      <button type="button" onclick={retry}>retry resume sessions</button>
    </div>
  {:else if rows.length === 0}
    <div class="nsresempty">no resumable sessions</div>
  {:else}
    {#each rows as row, index (row.sessionId)}
      <div
        class:live={row.active}
        class:sel={row.sessionId === value || index === activeIndex}
        class="nsresrow"
        data-session-id={row.sessionId}
        role="option"
        aria-selected={row.sessionId === value}
        tabindex="0"
        onfocus={() => {
          activeIndex = index;
        }}
        onkeydown={keydown}
        ondblclick={() => {
          void openPreview(row);
        }}
        onclick={() => {
          choose(row);
        }}
      >
        <div class="nsrestitle">{row.title ?? row.sessionId}</div>
        <div class="nsresmeta">
          {#if row.active}<span class="nsreschip live">live</span>{/if}
          <span class="nsreschip">{row.harness}</span>
          {#if row.model !== null}<span class="nsreschip"
              >{row.model.displayName}</span
            >{/if}
          {#if row.effort !== null}<span class="nsreschip">{row.effort}</span
            >{/if}
          {#if row.account !== null}<span class="nsreschip"
              >{row.account.displayName}</span
            >{/if}
          <span class="nsresago">{timeAgo(row.lastActivityAt)}</span>
        </div>
      </div>
    {/each}
  {/if}
</div>

{#if preview !== null}
  <div
    class="nspvback"
    role="presentation"
    onclick={(event) => {
      if (event.target === event.currentTarget) closePreview();
    }}
  >
    <div
      bind:this={previewPanel}
      class="nspvpanel"
      role="dialog"
      aria-modal="true"
      aria-label={`Preview ${preview.title ?? preview.sessionId}`}
      tabindex="-1"
      onkeydown={previewKeydown}
    >
      <div class="nspvhead">
        <div class="nspvtitle">{preview.title ?? preview.sessionId}</div>
        <button
          bind:this={previewClose}
          class="nspvx"
          type="button"
          aria-label="close preview"
          onclick={closePreview}>✕</button
        >
      </div>
      <div class="nspvbody">
        {#if previewLoading}
          <div class="nspreview-empty">loading history…</div>
        {:else if previewFailure !== null}
          <div class="nspreview-empty">could not load history</div>
        {:else if previewItems.length === 0}
          <div class="nspreview-empty">no history yet</div>
        {:else}
          <div class="stream">
            {#each previewItems as item (item.key)}
              <FeedItem
                presentation={item}
                extraClass=""
                defaultOpen={false}
                rewindModes={[]}
                rewindOpen={false}
                onOpenRewind={undefined}
                onCancelRewind={undefined}
                onRewind={undefined}
              />
            {/each}
          </div>
        {/if}
      </div>
    </div>
  </div>
{/if}
