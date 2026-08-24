import { SvelteMap, SvelteSet } from 'svelte/reactivity';

import {
  hideDirectory,
  readGlobalApplication,
  setGlobalNotifications,
} from '../api/application';
import { GlobalStream } from '../api/global-stream';
import { readHarnesses } from '../api/harnesses';
import { launchSession } from '../api/new-session';
import { closeSession } from '../api/controls';
import { readSession, readSessionList } from '../api/session-data';
import type { GlobalStreamDelta } from '../api/stream-decoder';
import type { TelemetryFields } from '../api/browser-telemetry';
import type { GlobalApplication } from '../application/model';
import type { HarnessDescription } from '../harnesses/model';
import type { LaunchDisplay, LaunchInput } from '../new-session/model';
import { reduceGlobalDelta } from '../sessions/global-reducer';
import type { SessionSnapshot } from '../sessions/model';
import type { SessionViewState } from '../sessions/session-view-state.svelte';
import { BrowserAudit } from '../shared/browser/browser-audit';
import {
  newClientId,
  newRequestId,
  stableDeviceId,
} from '../shared/browser/identity';
import {
  OptimisticActionTracker,
  reportClientFailure,
} from '../shared/browser/optimistic-action';
import { PresenceController } from '../shared/browser/presence';
import {
  browserNotificationPermission,
  PushNotificationController,
} from '../shared/browser/push-notifications';
import { formatRoute, isSessionRoute, parseHash } from './route';
import type { Route } from './route';

export type ConnectionState = 'connecting' | 'connected' | 'disconnected';
export type LoadState = 'idle' | 'loading' | 'ready' | 'failed';

const NO_HIDDEN_DIRECTORIES: ReadonlyMap<string, number> = new Map();
const LAUNCH_TIMEOUT_MS = 120_000;
const TOAST_LIFETIME_MS = 7_000;

export type ToastNotice = {
  readonly id: number;
  readonly kind: 'ask' | 'done' | '';
  readonly heading: string;
  readonly detail: string;
  readonly action: (() => void) | null;
};

export type PendingLaunch = {
  readonly generation: number;
  readonly input: LaunchInput;
  readonly display: LaunchDisplay;
  readonly startedAt: number;
  readonly knownSessionIds: ReadonlySet<string>;
  readonly liveSessionIds: ReadonlySet<string>;
  readonly quiet: boolean;
  readonly windowId: string | null;
};

export class AppState {
  private globalStream: GlobalStream | null = null;
  private bootId: string | null = null;
  private readonly adoptingSessions = new SvelteSet<string>();
  private readonly closeTrackers = new SvelteMap<
    SessionSnapshot['session']['sessionId'],
    OptimisticActionTracker
  >();
  private harnessRequest: Promise<void> | null = null;
  private launchTimer: ReturnType<typeof setTimeout> | null = null;
  private launchGeneration = 0;
  private toastSequence = 0;
  private readonly toastTimers = new SvelteMap<
    number,
    ReturnType<typeof setTimeout>
  >();
  readonly clientId = newClientId();
  readonly deviceId = stableDeviceId(localStorage, this.clientId);
  readonly audit = new BrowserAudit(this.clientId, this.deviceId, () =>
    this.connectionFacts(),
  );
  private readonly presence = new PresenceController(this.deviceId, () =>
    isSessionRoute(this.route) ? this.route.sessionId : null,
  );
  private readonly push = new PushNotificationController(
    this.deviceId,
    (error) => {
      this.audit.record(null, 'push.fail', { error: message(error) });
    },
  );

  route = $state<Route>({ kind: 'list' });
  connection = $state<ConnectionState>('connecting');
  notificationsEnabled = $state(true);
  notificationsBusy = $state(false);
  browserNotificationPermission = $state<
    NotificationPermission | 'unavailable'
  >(browserNotificationPermission());
  activeSession = $state<SessionViewState | null>(null);
  sessions = $state<readonly SessionSnapshot[]>([]);
  readonly closingSessions = new SvelteSet<
    SessionSnapshot['session']['sessionId']
  >();
  sessionCursor = $state(0);
  listState = $state<LoadState>('idle');
  applicationState = $state<LoadState>('idle');
  application = $state<GlobalApplication | null>(null);
  harnesses = $state<readonly HarnessDescription[]>([]);
  harnessState = $state<LoadState>('idle');
  pendingLaunch = $state<PendingLaunch | null>(null);
  launchFailure = $state<string | null>(null);
  toasts = $state<readonly ToastNotice[]>([]);
  newSessionRequest = $state<{
    readonly sequence: number;
    readonly workingDirectory: string;
  } | null>(null);

  readonly inSession = $derived(isSessionRoute(this.route));
  readonly hiddenDirectories = $derived(
    this.application?.preferences.hiddenDirectories ?? NO_HIDDEN_DIRECTORIES,
  );

  constructor(initialHash: string) {
    this.route = parseHash(initialHash);
  }

  readHash(hash: string): void {
    const route = parseHash(hash);
    if (this.pendingLaunch !== null && route.kind !== 'launching')
      this.pendingLaunch = { ...this.pendingLaunch, quiet: true };
    this.route = route;
    this.presence.beat();
  }

  async initialize(signal: AbortSignal): Promise<void> {
    this.presence.start();
    void this.push.start().then(() => {
      this.browserNotificationPermission = browserNotificationPermission();
      this.push.present(this.sessions);
    });
    document.addEventListener('visibilitychange', this.refreshWhenVisible);
    this.audit.record(null, 'boot', {
      online: navigator.onLine,
      visibility: document.visibilityState,
    });
    const applicationRequest = this.loadApplication(signal);
    const harnessRequest = this.loadHarnesses(signal);
    await this.loadSessions(signal);
    if (!signal.aborted) {
      this.openGlobalStream(
        this.listState === 'ready' ? this.sessionCursor : 0,
        signal,
      );
    }
    await Promise.all([applicationRequest, harnessRequest]);
  }

  async loadSessions(signal?: AbortSignal): Promise<void> {
    this.listState = 'loading';
    try {
      const list = await readSessionList(signal);
      this.sessions = list.sessions;
      this.sessionCursor = list.cursor;
      this.listState = 'ready';
      this.push.present(this.sessions);
    } catch {
      this.listState = 'failed';
    }
  }

  async loadApplication(signal?: AbortSignal): Promise<void> {
    this.applicationState = 'loading';
    try {
      const application = await readGlobalApplication(signal);
      const hadApplication = this.application !== null;
      const previousNotice = this.application?.notifications.latest ?? null;
      this.application = application;
      this.notificationsEnabled = application.notifications.enabled;
      this.presence.setLifetime(application.preferences.limits.presenceSeconds);
      this.applicationState = 'ready';
      const notice = application.notifications.latest;
      if (
        hadApplication &&
        notice !== null &&
        previousNotice?.revision !== notice.revision
      )
        this.announceNotice(notice);
    } catch {
      this.applicationState = 'failed';
    }
  }

  async setNotificationsEnabled(enabled: boolean): Promise<void> {
    if (this.notificationsBusy || enabled === this.notificationsEnabled) return;
    this.notificationsBusy = true;
    try {
      await setGlobalNotifications(enabled);
      this.notificationsEnabled = enabled;
      if (this.application !== null)
        this.application = {
          ...this.application,
          notifications: { ...this.application.notifications, enabled },
        };
      this.showToast(
        'done',
        enabled ? 'alerts on' : 'alerts off',
        enabled ? 'every session can notify' : 'all sessions silenced',
      );
    } catch (error) {
      this.showToast('ask', 'alerts toggle failed', message(error));
    } finally {
      this.notificationsBusy = false;
    }
  }

  async requestBrowserNotifications(): Promise<void> {
    try {
      this.browserNotificationPermission = await this.push.requestPermission();
    } catch (error) {
      this.browserNotificationPermission = browserNotificationPermission();
      this.audit.record(null, 'push.fail', { error: message(error) });
    }
  }

  async hideWorkingDirectory(workingDirectory: string): Promise<void> {
    const application = this.application;
    if (application === null) return;
    const optimistic = new SvelteMap(application.preferences.hiddenDirectories);
    optimistic.set(workingDirectory, Date.now() / 1_000);
    this.replaceHiddenDirectories(optimistic);
    try {
      this.replaceHiddenDirectories(await hideDirectory(workingDirectory));
    } catch (error) {
      const restored = new SvelteMap(
        this.application?.preferences.hiddenDirectories ?? optimistic,
      );
      restored.delete(workingDirectory);
      this.replaceHiddenDirectories(restored);
      this.showToast('ask', 'hide failed', message(error));
    }
  }

  showToast(
    kind: ToastNotice['kind'],
    heading: string,
    detail = '',
    action: (() => void) | null = null,
  ): void {
    const id = (this.toastSequence += 1);
    this.toasts = [...this.toasts, { id, kind, heading, detail, action }];
    this.toastTimers.set(
      id,
      setTimeout(() => {
        this.dismissToast(id);
      }, TOAST_LIFETIME_MS),
    );
  }

  dismissToast(id: number, action: (() => void) | null = null): void {
    const timer = this.toastTimers.get(id);
    if (timer !== undefined) clearTimeout(timer);
    this.toastTimers.delete(id);
    this.toasts = this.toasts.filter((toast) => toast.id !== id);
    action?.();
  }

  async loadHarnesses(signal?: AbortSignal): Promise<void> {
    if (this.harnessState === 'ready') {
      return;
    }
    if (this.harnessRequest !== null) {
      return this.harnessRequest;
    }
    this.harnessState = 'loading';
    const request = this.fetchHarnesses(signal);
    this.harnessRequest = request;
    try {
      await request;
    } finally {
      if (this.harnessRequest === request) {
        this.harnessRequest = null;
      }
    }
  }

  private async fetchHarnesses(signal?: AbortSignal): Promise<void> {
    try {
      this.harnesses = await readHarnesses(signal);
      this.harnessState = 'ready';
    } catch {
      this.harnessState = 'failed';
    }
  }

  private openGlobalStream(cursor: number, signal: AbortSignal): void {
    if (typeof EventSource === 'undefined') {
      return;
    }
    this.globalStream?.close();
    this.connection = 'connecting';
    this.globalStream = new GlobalStream(cursor, {
      opened: () => {
        this.connection = 'connected';
        this.audit.markStream('global', true);
      },
      disconnected: () => {
        this.connection = 'disconnected';
        this.audit.markStream('global', false);
      },
      delta: (frame) => {
        this.applyGlobalDelta(frame);
      },
      ready: (bootId) => {
        if (this.bootId === null) {
          this.bootId = bootId;
          this.audit.record(null, 'hello', { boot: bootId });
        } else if (this.bootId !== bootId) {
          this.audit.record(null, 'stale', {
            previous_boot: this.bootId,
            next_boot: bootId,
          });
          void this.audit.flush();
          this.bootId = bootId;
          window.location.reload();
        }
      },
      invalid: (error) => {
        this.connection = 'disconnected';
        this.audit.record(null, 'sse.invalid', {
          stream: 'global',
          error: error.message,
        });
      },
    });
    signal.addEventListener(
      'abort',
      () => {
        this.globalStream?.close();
        this.globalStream = null;
      },
      { once: true },
    );
  }

  private applyGlobalDelta(frame: GlobalStreamDelta): void {
    const result = reduceGlobalDelta(this.sessions, frame);
    this.sessions = result.sessions;
    this.push.present(this.sessions);
    this.reconcileSessionCloses();
    for (const id of result.adopt) {
      void this.adoptSession(id);
    }
    this.resolvePendingLaunch();
  }

  async requestSessionClose(
    sessionId: SessionSnapshot['session']['sessionId'],
  ): Promise<boolean> {
    if (this.closingSessions.has(sessionId)) return false;
    this.closingSessions.add(sessionId);
    const tracker = new OptimisticActionTracker(sessionId, 'close');
    this.closeTrackers.set(sessionId, tracker);
    try {
      const result = await closeSession(sessionId, newRequestId());
      if (result.status !== 'acknowledged') {
        this.dropSessionClose(sessionId, result.reason ?? result.status);
        this.showToast(
          'ask',
          'close failed',
          result.reason ?? 'the session did not confirm the action',
        );
        return false;
      }
      this.showToast('done', 'session closed', 'terminal tab closed');
      return true;
    } catch (error) {
      this.dropSessionClose(sessionId, 'failed');
      reportClientFailure(sessionId, 'close', error);
      this.showToast('ask', 'close failed', message(error));
      return false;
    }
  }

  private async adoptSession(
    id: SessionSnapshot['session']['sessionId'],
  ): Promise<void> {
    if (this.adoptingSessions.has(id)) {
      return;
    }
    this.adoptingSessions.add(id);
    try {
      const snapshot = await readSession(id);
      if (
        !this.sessions.some(
          (known) => known.session.sessionId === snapshot.session.sessionId,
        )
      ) {
        this.sessions = [...this.sessions, snapshot];
      }
      this.resolvePendingLaunch();
    } catch (error) {
      // A later stream frame can retry adoption. The audit channel owns detail.
      this.audit.record(id, 'session.adopt.fail', {
        error: error instanceof Error ? error.message : String(error),
      });
    } finally {
      this.adoptingSessions.delete(id);
    }
  }

  navigate(
    route: Exclude<Route, { readonly kind: 'not-found' }>,
    replace = false,
  ): void {
    const hash = formatRoute(route);
    if (replace) {
      window.location.replace(hash);
      return;
    }
    window.location.hash = hash;
    this.readHash(hash);
  }

  async beginLaunch(input: LaunchInput, display: LaunchDisplay): Promise<void> {
    this.clearLaunchTimer();
    this.launchFailure = null;
    const watch = this.newLaunchWatch(input, display);
    this.pendingLaunch = watch;
    this.navigate({ kind: 'launching' });
    this.launchTimer = setTimeout(() => {
      if (this.pendingLaunch?.generation !== watch.generation) return;
      this.pendingLaunch = null;
      this.launchFailure = 'the session never appeared';
    }, LAUNCH_TIMEOUT_MS);
    try {
      const result = await launchSession(input);
      if (result.status !== 'started')
        throw new Error(result.reason ?? 'the launch was rejected');
      if (this.ownsLaunch(watch.generation)) {
        this.pendingLaunch = { ...watch, windowId: result.windowId };
        this.resolvePendingLaunch();
      }
    } catch (error) {
      if (this.ownsLaunch(watch.generation)) this.pendingLaunch = null;
      this.clearLaunchTimer();
      this.launchFailure =
        error instanceof Error ? error.message : String(error);
      if (this.route.kind === 'launching') this.navigate({ kind: 'list' });
      throw error;
    }
  }

  async beginComposerResume(
    input: LaunchInput,
    display: LaunchDisplay,
    timedOut: () => void,
  ): Promise<void> {
    this.clearLaunchTimer();
    this.launchFailure = null;
    const watch = this.newLaunchWatch(input, display);
    this.pendingLaunch = watch;
    this.launchTimer = setTimeout(() => {
      if (!this.ownsLaunch(watch.generation)) return;
      this.pendingLaunch = null;
      this.clearLaunchTimer();
      timedOut();
    }, LAUNCH_TIMEOUT_MS);
    try {
      const result = await launchSession(input);
      if (result.status !== 'started')
        throw new Error(result.reason ?? 'the launch was rejected');
      if (this.ownsLaunch(watch.generation)) {
        this.pendingLaunch = { ...watch, windowId: result.windowId };
        this.resolvePendingLaunch();
      }
    } catch (error) {
      if (this.ownsLaunch(watch.generation)) this.pendingLaunch = null;
      this.clearLaunchTimer();
      throw error;
    }
  }

  cancelLaunchFailure(): void {
    this.launchFailure = null;
  }

  requestNewSession(workingDirectory = ''): void {
    this.newSessionRequest = { sequence: Date.now(), workingDirectory };
  }

  private resolvePendingLaunch(): void {
    const watch = this.pendingLaunch;
    if (watch === null) return;
    const matched =
      (watch.input.resumeSessionId === null
        ? undefined
        : this.sessions.find(
            (snapshot) =>
              snapshot.session.sessionId === watch.input.resumeSessionId,
          )) ??
      this.sessions.find((snapshot) => {
        const id = snapshot.session.sessionId;
        return (
          snapshot.session.workingDirectory === watch.input.workingDirectory &&
          (!watch.knownSessionIds.has(id) ||
            (snapshot.live && !watch.liveSessionIds.has(id)))
        );
      });
    if (matched === undefined) return;
    this.pendingLaunch = null;
    this.clearLaunchTimer();
    if (watch.quiet) return;
    this.navigate(
      {
        kind: 'session',
        sessionId: matched.session.sessionId,
        tab: 'mirror',
      },
      true,
    );
  }

  private newLaunchWatch(
    input: LaunchInput,
    display: LaunchDisplay,
  ): PendingLaunch {
    return {
      generation: (this.launchGeneration += 1),
      input,
      display,
      startedAt: Date.now(),
      knownSessionIds: new SvelteSet(
        this.sessions.map((snapshot) => snapshot.session.sessionId),
      ),
      liveSessionIds: new SvelteSet(
        this.sessions
          .filter((snapshot) => snapshot.live)
          .map((snapshot) => snapshot.session.sessionId),
      ),
      quiet: false,
      windowId: null,
    };
  }

  private clearLaunchTimer(): void {
    if (this.launchTimer === null) return;
    clearTimeout(this.launchTimer);
    this.launchTimer = null;
  }

  private ownsLaunch(generation: number): boolean {
    return this.pendingLaunch?.generation === generation;
  }

  registerSession(view: SessionViewState): () => void {
    this.activeSession = view;
    return () => {
      if (this.activeSession === view) this.activeSession = null;
    };
  }

  destroy(): void {
    this.clearLaunchTimer();
    this.globalStream?.close();
    this.globalStream = null;
    this.presence.destroy();
    this.push.destroy();
    document.removeEventListener('visibilitychange', this.refreshWhenVisible);
    for (const timer of this.toastTimers.values()) clearTimeout(timer);
    this.toastTimers.clear();
    for (const tracker of this.closeTrackers.values()) tracker.cancel();
    this.closeTrackers.clear();
    this.closingSessions.clear();
    void this.audit.flush();
    this.audit.destroy();
  }

  private replaceHiddenDirectories(
    hiddenDirectories: ReadonlyMap<string, number>,
  ): void {
    if (this.application === null) return;
    this.application = {
      ...this.application,
      preferences: {
        ...this.application.preferences,
        hiddenDirectories,
      },
    };
  }

  private reconcileSessionCloses(): void {
    for (const sessionId of this.closingSessions) {
      const snapshot = this.sessions.find(
        (candidate) => candidate.session.sessionId === sessionId,
      );
      if (snapshot?.live === true) continue;
      this.closeTrackers.get(sessionId)?.settle('reconciled');
      this.closeTrackers.delete(sessionId);
      this.closingSessions.delete(sessionId);
    }
  }

  private dropSessionClose(
    sessionId: SessionSnapshot['session']['sessionId'],
    reason: string,
  ): void {
    this.closeTrackers.get(sessionId)?.settle('dropped', reason);
    this.closeTrackers.delete(sessionId);
    this.closingSessions.delete(sessionId);
  }

  private announceNotice(
    notice: NonNullable<GlobalApplication['notifications']['latest']>,
  ): void {
    const visible = document.visibilityState === 'visible';
    const focused = document.hasFocus();
    this.audit.record(notice.sessionId, 'notify.recv', {
      kind: notice.kind,
      shown: visible && focused,
      vis: visible,
      focus: focused,
    });
    if (!visible || !focused) return;
    const asking = notice.kind === 'asking';
    const subject = notice.project || notice.sessionId;
    this.showToast(
      asking ? 'ask' : 'done',
      `${subject}${asking ? ' needs you' : ' is done'}`,
      notice.title ||
        (asking ? 'a question is waiting' : 'finished — your turn'),
      () => {
        this.navigate({
          kind: 'session',
          sessionId: notice.sessionId,
          tab: 'mirror',
        });
      },
    );
  }

  private refreshWhenVisible = (): void => {
    if (document.visibilityState !== 'visible') return;
    void this.loadApplication();
    void this.activeSession?.refreshApplication();
  };

  private connectionFacts(): TelemetryFields {
    return {
      online: navigator.onLine,
      visibility: document.visibilityState,
      view: this.route.kind,
      event_streams: 1 + (this.activeSession === null ? 0 : 1),
      connected: this.connection === 'connected',
    };
  }
}

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
