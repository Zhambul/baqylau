import { SvelteMap, SvelteSet } from 'svelte/reactivity';

import type {
  OptimisticAction,
  TelemetryFields,
} from '../api/browser-telemetry';
import { interrupt } from '../api/controls';
import { readEntryPage } from '../api/entries';
import { readHarnessCatalog } from '../api/harnesses';
import { readSessionApplication } from '../api/session-application';
import {
  saveNotificationsMuted,
  saveTasksHidden,
  saveViewMode,
} from '../api/session-preferences';
import { readSession } from '../api/session-data';
import { SessionStream } from '../api/session-stream';
import type { SessionStreamDelta } from '../api/stream-decoder';
import type { AppState, LoadState } from '../app/app-state.svelte';
import type {
  ActorId,
  ClientId,
  RequestId,
  SessionId,
} from '../app/domain-ids';
import type {
  SessionApplication,
  ViewMode,
} from '../application/session-model';
import type { Entry } from '../entries/model';
import type { ControlOutcome } from '../controls/model';
import { pendingAttention } from '../entries/attention';
import {
  buildFeedItems,
  planDensity,
  visibleDensityCount,
} from '../entries/feed-model';
import type {
  HarnessCatalog,
  HarnessDescription,
  SessionCapabilities,
} from '../harnesses/model';
import { OptimisticActionTracker } from '../shared/browser/optimistic-action';
import { reportClientFailure } from '../shared/browser/optimistic-action';
import { newRequestId } from '../shared/browser/identity';
import { sortedChildActors } from './agent-presentation';
import {
  appendOlderEntries,
  entriesForActor,
  initialEntriesNewestFirst,
  mergeActors,
  prependLiveEntries,
} from './session-reducer';
import type { Actor, Session, SessionSnapshot } from './model';
import {
  deliveredPrompt,
  mergeQueuedPrompts,
  promptMatches,
} from './optimistic-prompts';
import { foldShellEntries, jobFolds, monitorFolds } from './shell-fold';

const HISTORY_FETCH = 40;
const HISTORY_REQUEST_LIMIT = 400;
const HISTORY_REQUEST_TRIES = 6;
const FEED_FILL_MINIMUM = 15;
const BUSY_STATUSES = new Set([
  'thinking',
  'working',
  'executing',
  'awaiting_background',
]);

const NO_CAPABILITIES: SessionCapabilities = {
  send: false,
  interrupt: false,
  background: false,
  close: false,
  rename: false,
  autoname: false,
  rewind: false,
  compact: false,
  model: false,
  effort: false,
  answer: false,
  plan: false,
};

export type PendingPrompt = {
  readonly requestId: RequestId;
  readonly text: string;
  readonly tracker: OptimisticActionTracker;
};

export type PendingQueuedPrompt = {
  readonly requestId: RequestId;
  readonly text: string;
};

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function cancelled(signal: AbortSignal): boolean {
  return signal.aborted;
}

export class SessionViewState {
  private stream: SessionStream | null = null;
  private readonly pendingActions = new SvelteMap<
    string,
    OptimisticActionTracker
  >();
  readonly sessionId: SessionId;
  readonly actorId: ActorId | undefined;
  readonly clientId: ClientId;
  private readonly appState: AppState;

  session = $state<Session | null>(null);
  actors = $state<readonly Actor[]>([]);
  live = $state<boolean | null>(null);
  repository = $state<SessionSnapshot['repository']>(null);
  entries = $state<readonly Entry[]>([]);
  cursor = $state(0);
  oldestCursor = $state<number | null>(null);
  loadState = $state<LoadState>('idle');
  loadFailure = $state<string | null>(null);
  application = $state<SessionApplication | null>(null);
  applicationState = $state<LoadState>('idle');
  applicationFailure = $state<string | null>(null);
  harness = $state<HarnessDescription | null>(null);
  catalog = $state<HarnessCatalog | null>(null);
  catalogState = $state<LoadState>('idle');
  catalogFailure = $state<string | null>(null);
  capabilities = $state<SessionCapabilities | null>(null);
  streamState = $state<LoadState>('idle');
  streamFailure = $state<string | null>(null);
  loadingOlder = $state(false);
  olderFailure = $state<string | null>(null);
  composerOverride = $state<{
    readonly sequence: number;
    readonly text: string;
  } | null>(null);
  rewindPicking = $state(false);
  dismissMenusSequence = $state(0);
  pendingPrompts = $state<readonly PendingPrompt[]>([]);
  pendingQueuedPrompts = $state<readonly PendingQueuedPrompt[]>([]);

  readonly shellFolds = $derived(
    foldShellEntries(
      this.entries,
      this.scopedActor?.background.runningShellIds ?? [],
    ),
  );
  readonly monitors = $derived(monitorFolds(this.shellFolds));
  readonly jobs = $derived(jobFolds(this.shellFolds));
  readonly attention = $derived(pendingAttention(this.entries));
  readonly feedItems = $derived.by(() => {
    const actors = new SvelteMap<ActorId, string>();
    for (const actor of this.actors) actors.set(actor.actorId, actor.name);
    return buildFeedItems(this.entries, actors, this.shellFolds);
  });
  readonly visibleFeedCount = $derived(
    visibleDensityCount(
      planDensity(
        this.feedItems,
        this.application?.preferences.viewMode ?? 'default',
        BUSY_STATUSES.has(this.scopedActor?.status ?? ''),
        new SvelteSet(),
      ),
    ),
  );
  constructor(
    sessionId: SessionId,
    actorId: ActorId | undefined,
    appState: AppState,
  ) {
    this.sessionId = sessionId;
    this.actorId = actorId;
    this.appState = appState;
    this.clientId = appState.clientId;
  }

  get scopedActorId(): ActorId | null {
    return this.actorId ?? this.session?.leadActorId ?? null;
  }

  get uploadLimit(): number | null {
    return this.appState.application?.preferences.limits.uploadBytes ?? null;
  }

  get scopedActor(): Actor | null {
    return (
      this.actors.find((actor) => actor.actorId === this.scopedActorId) ?? null
    );
  }

  get leadActor(): Actor | null {
    return (
      this.actors.find(
        (actor) => actor.actorId === this.session?.leadActorId,
      ) ?? null
    );
  }

  get childActors(): readonly Actor[] {
    return sortedChildActors(
      this.actors.filter(
        (actor) => actor.actorId !== this.session?.leadActorId,
      ),
    );
  }

  async initialize(signal: AbortSignal): Promise<void> {
    this.loadState = 'loading';
    let snapshot: SessionSnapshot;
    try {
      snapshot = await readSession(this.sessionId, signal);
    } catch (error) {
      if (cancelled(signal)) return;
      this.loadState = 'failed';
      this.loadFailure = message(error);
      this.connect(0, signal);
      return;
    }
    if (cancelled(signal)) return;
    this.applySnapshot(snapshot);

    try {
      const page = await readEntryPage(this.sessionId, {
        at: snapshot.cursor,
        limit: HISTORY_FETCH,
        signal,
      });
      if (cancelled(signal)) return;
      const actorId = this.scopedActorId;
      const entries =
        actorId === null ? page.items : entriesForActor(page.items, actorId);
      this.entries = initialEntriesNewestFirst(entries);
      this.oldestCursor = page.hasMore ? page.oldestCursor : null;
    } catch (error) {
      if (cancelled(signal)) return;
      this.loadFailure = message(error);
    }

    await Promise.all([
      this.loadApplication(signal),
      this.loadCatalogAndCapabilities(signal),
    ]);
    if (cancelled(signal)) return;
    this.loadState = this.loadFailure === null ? 'ready' : 'failed';
    this.connect(snapshot.cursor, signal);
  }

  destroy(): void {
    this.stream?.close();
    this.stream = null;
    for (const prompt of this.pendingPrompts) prompt.tracker.cancel();
    for (const tracker of this.pendingActions.values()) tracker.cancel();
    this.pendingActions.clear();
    this.pendingPrompts = [];
    this.pendingQueuedPrompts = [];
  }

  async loadOlder(want = HISTORY_FETCH): Promise<void> {
    if (this.loadingOlder || this.oldestCursor === null) return;
    this.loadingOlder = true;
    this.olderFailure = null;
    const startedVisible = this.visibleFeedCount;
    let requestSize = HISTORY_FETCH;
    let tries = 0;
    let before = this.oldestCursor;
    try {
      while (tries < HISTORY_REQUEST_TRIES) {
        const page = await readEntryPage(this.sessionId, {
          before,
          limit: requestSize,
        });
        const actorId = this.scopedActorId;
        const entries =
          actorId === null ? page.items : entriesForActor(page.items, actorId);
        this.entries = appendOlderEntries(this.entries, entries);
        tries += 1;
        const gained = this.visibleFeedCount - startedVisible;
        if (!page.hasMore) {
          this.oldestCursor = null;
          break;
        }
        this.oldestCursor = page.oldestCursor;
        before = page.oldestCursor;
        if (gained >= want) break;
        const blocksPerVisible =
          gained > 0 ? requestSize / gained : HISTORY_REQUEST_LIMIT;
        requestSize = Math.max(
          HISTORY_FETCH,
          Math.min(
            Math.ceil((want - gained) * blocksPerVisible),
            HISTORY_REQUEST_LIMIT,
          ),
        );
      }
    } catch (error) {
      this.olderFailure = message(error);
    } finally {
      this.loadingOlder = false;
    }
  }

  async setViewMode(viewMode: ViewMode): Promise<void> {
    const application = this.application;
    if (application === null || application.preferences.viewMode === viewMode)
      return;
    this.application = {
      ...application,
      preferences: { ...application.preferences, viewMode },
    };
    await Promise.resolve();
    if (
      viewMode !== 'verbose' &&
      this.visibleFeedCount < FEED_FILL_MINIMUM &&
      this.oldestCursor !== null
    )
      void this.loadOlder(FEED_FILL_MINIMUM);
    try {
      await saveViewMode(this.sessionId, viewMode);
    } catch (error) {
      this.application = application;
      this.applicationFailure = message(error);
    }
  }

  async refreshApplication(): Promise<void> {
    await this.loadApplication(new AbortController().signal);
  }

  async retryCatalog(): Promise<void> {
    if (this.catalogState === 'loading') return;
    await this.loadCatalogAndCapabilities(new AbortController().signal);
  }

  async setNotificationsMuted(muted: boolean): Promise<void> {
    const application = this.application;
    if (
      application === null ||
      application.preferences.notificationsMuted === muted
    )
      return;
    this.application = {
      ...application,
      preferences: { ...application.preferences, notificationsMuted: muted },
    };
    try {
      await saveNotificationsMuted(this.sessionId, muted);
    } catch (error) {
      this.application = application;
      this.applicationFailure = message(error);
    }
  }

  async setTasksHidden(hidden: boolean): Promise<void> {
    const application = this.application;
    if (application === null || application.preferences.tasksHidden === hidden)
      return;
    this.application = {
      ...application,
      preferences: { ...application.preferences, tasksHidden: hidden },
    };
    try {
      await saveTasksHidden(this.sessionId, hidden);
    } catch (error) {
      this.application = application;
      this.applicationFailure = message(error);
    }
  }

  restoreComposer(text: string): void {
    this.composerOverride = { sequence: Date.now(), text };
  }

  consumeComposerOverride(sequence: number): void {
    if (this.composerOverride?.sequence === sequence)
      this.composerOverride = null;
  }

  setRewindPicking(picking: boolean): void {
    this.rewindPicking = picking;
  }

  dismissMenus(): void {
    this.dismissMenusSequence += 1;
  }

  async interruptTurn(): Promise<ControlOutcome> {
    try {
      const result = await interrupt(this.sessionId, newRequestId());
      if (
        result.status === 'acknowledged' &&
        result.kind === 'delivery' &&
        result.restoredText.trim().length > 0
      )
        this.restoreComposer(result.restoredText);
      return result;
    } catch (error) {
      reportClientFailure(this.sessionId, 'interrupt', error);
      throw error;
    }
  }

  beginRewind(): void {
    this.setRewindPicking(true);
  }

  showPendingPrompt(requestId: RequestId, text: string): void {
    if (text.length === 0) return;
    this.pendingPrompts = [
      {
        requestId,
        text,
        tracker: new OptimisticActionTracker(
          this.sessionId,
          'composer',
          text.length,
        ),
      },
      ...this.pendingPrompts,
    ];
  }

  settlePendingPrompt(
    requestId: RequestId,
    phase: 'reconciled' | 'dropped',
    reason: string | null = null,
  ): void {
    const prompt = this.pendingPrompts.find(
      (candidate) => candidate.requestId === requestId,
    );
    if (prompt === undefined) return;
    prompt.tracker.settle(phase, reason);
    this.pendingPrompts = this.pendingPrompts.filter(
      (candidate) => candidate.requestId !== requestId,
    );
  }

  queuePendingPrompt(requestId: RequestId, text: string): void {
    if (text.length === 0) return;
    this.pendingQueuedPrompts = [
      ...this.pendingQueuedPrompts,
      { requestId, text },
    ];
  }

  get queuedPromptTexts(): readonly string[] {
    const persisted = this.application?.composer.queue?.items ?? [];
    const delivered = [...this.entries]
      .sort((left, right) => left.cursor - right.cursor)
      .flatMap((entry) => {
        const text = deliveredPrompt(entry);
        return text === null ? [] : [text];
      });
    return mergeQueuedPrompts(
      persisted,
      this.pendingQueuedPrompts,
      delivered,
    ).map((item) => item.text);
  }

  recordBrowserEvent(name: string, details: TelemetryFields = {}): void {
    this.appState.audit.record(this.sessionId, name, details);
  }

  showPendingAction(
    action: Extract<OptimisticAction, 'answer' | 'plan'>,
    attentionId: string,
    characterCount: number | null = null,
  ): void {
    const key = pendingActionKey(action, attentionId);
    this.pendingActions.get(key)?.cancel();
    this.pendingActions.set(
      key,
      new OptimisticActionTracker(this.sessionId, action, characterCount),
    );
  }

  dropPendingAction(
    action: Extract<OptimisticAction, 'answer' | 'plan'>,
    attentionId: string,
    reason: string,
  ): void {
    const key = pendingActionKey(action, attentionId);
    this.pendingActions.get(key)?.settle('dropped', reason);
    this.pendingActions.delete(key);
  }

  private applySnapshot(snapshot: SessionSnapshot): void {
    this.session = snapshot.session;
    this.actors = snapshot.actors;
    this.live = snapshot.live;
    this.repository = snapshot.repository;
    this.cursor = snapshot.cursor;
  }

  private async loadApplication(signal: AbortSignal): Promise<void> {
    this.applicationState = 'loading';
    try {
      this.application = await readSessionApplication(this.sessionId, signal);
      if (!cancelled(signal)) this.applicationState = 'ready';
    } catch (error) {
      if (cancelled(signal)) return;
      this.applicationState = 'failed';
      this.applicationFailure = message(error);
    }
  }

  private async loadCatalogAndCapabilities(signal: AbortSignal): Promise<void> {
    this.catalogState = 'loading';
    await this.appState.loadHarnesses(signal);
    if (cancelled(signal)) return;
    const session = this.session;
    if (session === null) {
      this.catalogState = 'failed';
      this.catalogFailure = 'session facts are unavailable';
      this.capabilities = NO_CAPABILITIES;
      return;
    }
    this.harness =
      this.appState.harnesses.find((row) => row.name === session.harness) ??
      null;
    this.capabilities = this.harness?.capabilities ?? NO_CAPABILITIES;
    try {
      this.catalog = await readHarnessCatalog(
        session.harness,
        this.sessionId,
        session.workingDirectory,
        signal,
      );
      if (!cancelled(signal)) this.catalogState = 'ready';
    } catch (error) {
      if (cancelled(signal)) return;
      this.catalogState = 'failed';
      this.catalogFailure = message(error);
    }
  }

  private connect(cursor: number, signal: AbortSignal): void {
    if (typeof EventSource === 'undefined') return;
    this.stream?.close();
    this.streamState = 'loading';
    this.stream = new SessionStream(this.sessionId, cursor, {
      opened: () => {
        this.streamState = 'ready';
        this.streamFailure = null;
        this.appState.audit.markStream(
          `session:${this.sessionId}:${this.actorId ?? ''}`,
          true,
          this.sessionId,
          { actor_id: this.actorId ?? null },
        );
      },
      disconnected: () => {
        this.streamState = 'failed';
        this.appState.audit.markStream(
          `session:${this.sessionId}:${this.actorId ?? ''}`,
          false,
          this.sessionId,
          { actor_id: this.actorId ?? null },
        );
      },
      delta: (frame, nextCursor) => {
        this.applyDelta(frame, nextCursor);
      },
      invalid: (error) => {
        this.streamState = 'failed';
        this.streamFailure = error.message;
        this.recordBrowserEvent('sse.invalid', {
          stream: 'session',
          actor_id: this.actorId ?? null,
          error: error.message,
        });
      },
    });
    signal.addEventListener(
      'abort',
      () => {
        this.destroy();
      },
      { once: true },
    );
  }

  private applyDelta(frame: SessionStreamDelta, cursor: number): void {
    if (frame.session !== null) this.session = frame.session;
    this.actors = mergeActors(this.actors, frame.actors);
    this.cursor = cursor;
    const actorId = this.scopedActorId;
    const entries =
      actorId === null
        ? frame.entries
        : entriesForActor(frame.entries, actorId);
    this.reconcilePrompts(entries);
    this.reconcilePendingActions(entries);
    this.entries = prependLiveEntries(this.entries, entries);
  }

  private reconcilePrompts(entries: readonly Entry[]): void {
    for (const entry of entries) {
      const delivered = deliveredPrompt(entry);
      if (delivered === null) continue;
      const pending = this.pendingPrompts.find((prompt) =>
        promptMatches(delivered, prompt.text),
      );
      if (pending !== undefined)
        this.settlePendingPrompt(pending.requestId, 'reconciled');
      const queued = this.pendingQueuedPrompts.find((prompt) =>
        promptMatches(delivered, prompt.text),
      );
      if (queued !== undefined)
        this.pendingQueuedPrompts = this.pendingQueuedPrompts.filter(
          (prompt) => prompt.requestId !== queued.requestId,
        );
    }
  }

  private reconcilePendingActions(entries: readonly Entry[]): void {
    for (const entry of entries) {
      let action: 'answer' | 'plan' | null = null;
      let attentionId: string | null = null;
      if (entry.type === 'question_answered') {
        action = 'answer';
        attentionId = entry.body.attentionId;
      } else if (entry.type === 'plan_resolved') {
        action = 'plan';
        attentionId = entry.body.attentionId;
      }
      if (action === null || attentionId === null) continue;
      const key = pendingActionKey(action, attentionId);
      this.pendingActions.get(key)?.settle('reconciled');
      this.pendingActions.delete(key);
    }
  }
}

function pendingActionKey(
  action: Extract<OptimisticAction, 'answer' | 'plan'>,
  attentionId: string,
): string {
  return `${action}:${attentionId}`;
}
