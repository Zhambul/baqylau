<script lang="ts">
  import { SvelteMap, SvelteSet } from 'svelte/reactivity';

  import { applyRewind } from '../../api/controls';
  import type { ActorId } from '../../app/domain-ids';
  import type { ViewMode } from '../../application/session-model';
  import FeedItem from '../../entries/FeedItem.svelte';
  import {
    buildFeedItems,
    planDensity,
    type DensityUnit,
  } from '../../entries/feed-model';
  import RunSummary from '../../entries/RunSummary.svelte';
  import type { Entry } from '../../entries/model';
  import { newRequestId } from '../../shared/browser/identity';
  import type { SessionViewState } from '../session-view-state.svelte';

  const BUSY = new Set([
    'thinking',
    'working',
    'executing',
    'awaiting_background',
  ]);

  let { view }: { view: SessionViewState } = $props();

  const actorNames = $derived.by(() => {
    const names = new SvelteMap<ActorId, string>();
    for (const actor of view.actors) names.set(actor.actorId, actor.name);
    return names;
  });
  const entryByKey = $derived.by(() => {
    const entries = new SvelteMap<string, Entry>();
    for (const entry of view.entries) entries.set(entry.entryId, entry);
    return entries;
  });
  const items = $derived(
    buildFeedItems(view.entries, actorNames, view.shellFolds),
  );
  const mode = $derived(view.application?.preferences.viewMode ?? 'default');
  const busy = $derived(BUSY.has(view.scopedActor?.status ?? ''));
  const openRuns = new SvelteSet<string>();
  const units = $derived(planDensity(items, mode, busy, openRuns));
  let priorMode = $state<ViewMode | null>(null);
  let rewindEntry = $state<Entry | null>(null);
  let rewindFailure = $state<string | null>(null);
  let handledDismissSequence = $state(0);

  function observeOlder(node: HTMLDivElement): { destroy: () => void } {
    const observer = new IntersectionObserver((entries) => {
      if (
        entries.some((entry) => entry.isIntersecting) &&
        view.olderFailure === null
      )
        void view.loadOlder();
    });
    observer.observe(node);
    return {
      destroy: () => {
        observer.disconnect();
      },
    };
  }

  $effect(() => {
    if (priorMode === null) {
      priorMode = mode;
      return;
    }
    if (mode === priorMode) return;
    priorMode = mode;
    openRuns.clear();
  });

  $effect(() => {
    const sequence = view.dismissMenusSequence;
    if (sequence === handledDismissSequence) return;
    handledDismissSequence = sequence;
    rewindEntry = null;
  });

  function rewindCandidate(
    entry: Entry | undefined,
  ): entry is Extract<Entry, { readonly type: 'message' }> {
    return (
      entry?.type === 'message' &&
      entry.body.role === 'user' &&
      entry.body.phase !== 'synthetic'
    );
  }

  function newerPromptCount(target: Entry): number {
    const targetPosition = view.entries.findIndex(
      (entry) => entry.entryId === target.entryId,
    );
    return view.entries
      .slice(0, targetPosition < 0 ? 0 : targetPosition)
      .filter(rewindCandidate).length;
  }

  async function rewind(entry: Entry, rewindMode: string): Promise<void> {
    if (!rewindCandidate(entry)) return;
    rewindFailure = null;
    try {
      const result = await applyRewind(
        view.sessionId,
        newRequestId(),
        entry.body.messageId,
        entry.body.content.text,
        newerPromptCount(entry),
        rewindMode,
      );
      if (result.status !== 'acknowledged') {
        rewindFailure =
          result.reason ?? 'the session did not confirm the rewind';
        return;
      }
      if (result.kind === 'rewind' && result.restoredText.length > 0)
        view.restoreComposer(result.restoredText);
      view.setRewindPicking(false);
      rewindEntry = null;
    } catch (error) {
      rewindFailure = error instanceof Error ? error.message : String(error);
    }
  }

  function toggleRun(key: string): void {
    if (openRuns.has(key)) openRuns.delete(key);
    else openRuns.add(key);
  }

  function unitKey(unit: DensityUnit): string {
    return `${unit.kind}:${unit.kind === 'summary' ? unit.summary.key : unit.item.key}`;
  }
</script>

<div class:rwpick={view.rewindPicking} class="stream">
  {#each view.pendingPrompts as prompt (prompt.requestId)}
    <div class="msg prompt pending">
      <span class="who">you</span>
      <div class="md">
        <p>
          {#each prompt.text.split('\n') as line, index (`${String(index)}:${line}`)}
            {#if index > 0}<br />{/if}{line}
          {/each}
        </p>
      </div>
    </div>
  {/each}
  {#if units.length === 0 && view.pendingPrompts.length === 0}
    <div class="waiting">waiting for activity…</div>
  {:else if units.length > 0}
    {#each units as unit (unitKey(unit))}
      {#if unit.kind === 'summary'}
        <RunSummary
          summary={unit.summary}
          ontoggle={() => {
            toggleRun(unit.summary.key);
          }}
        />
      {:else if unit.kind === 'item'}
        {@const entry = entryByKey.get(unit.item.key)}
        <FeedItem
          presentation={unit.item}
          extraClass={unit.extraClass}
          defaultOpen={unit.defaultOpen}
          rewindModes={view.catalog?.rewindModes ?? []}
          rewindOpen={rewindEntry?.entryId === entry?.entryId}
          onOpenRewind={rewindCandidate(entry)
            ? () => {
                rewindEntry = entry;
              }
            : undefined}
          onCancelRewind={() => {
            rewindEntry = null;
          }}
          onRewind={entry === undefined
            ? undefined
            : (rewindMode: string) => {
                void rewind(entry, rewindMode);
              }}
        />
      {/if}
    {/each}
  {/if}
  {#if view.oldestCursor !== null}
    <div class="load-sentinel" use:observeOlder>
      {#if view.loadingOlder}
        <span
          class="feed-loader"
          role="status"
          aria-label="loading older activity"
        >
          <span class="feed-spinner" aria-hidden="true"></span>
        </span>
      {/if}
    </div>
  {/if}
  {#if view.olderFailure !== null}
    <div class="older-failure" role="alert">
      <span>could not load older activity</span>
      <button type="button" onclick={() => view.loadOlder()}>retry</button>
    </div>
  {/if}
  {#if rewindFailure !== null}
    <div class="empty" role="alert">{rewindFailure}</div>
  {/if}
</div>
