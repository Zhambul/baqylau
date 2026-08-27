<script lang="ts">
  import { onMount } from 'svelte';

  import AccountUsageStrip from '../application/components/AccountUsageStrip.svelte';
  import AttentionStrip from '../application/components/AttentionStrip.svelte';
  import LaunchingView from '../new-session/LaunchingView.svelte';
  import NewSessionModal from '../new-session/NewSessionModal.svelte';
  import type {
    LaunchDisplay,
    LaunchInput,
    NewSessionSeed,
  } from '../new-session/model';
  import SessionListView from '../sessions/components/SessionListView.svelte';
  import SessionView from '../sessions/components/SessionView.svelte';
  import SessionActions from '../sessions/components/SessionActions.svelte';
  import StatsView from '../stats/components/StatsView.svelte';
  import BrandMark from '../shared/components/BrandMark.svelte';
  import SunMark from '../shared/components/SunMark.svelte';
  import ToastStack from '../shared/components/ToastStack.svelte';
  import {
    fullscreenActive,
    fullscreenAvailable,
    toggleFullscreen,
  } from '../shared/browser/fullscreen';
  import { WakeLockController } from '../shared/browser/wake-lock';
  import {
    cycleLiveSession,
    handleReadlineKey,
  } from '../shared/browser/keyboard';
  import type { StandardControlOutcome } from '../controls/model';
  import type { SessionViewState } from '../sessions/session-view-state.svelte';
  import { AppState } from './app-state.svelte';
  import { setAppState } from './app-context';
  import { startupNavigation } from './route';

  const startup = startupNavigation(
    window.location.hash,
    window.location.search,
  );
  if (startup.consumeQuery)
    window.history.replaceState(
      null,
      '',
      `${window.location.pathname}${startup.hash}`,
    );
  const appState = new AppState(startup.hash);
  setAppState(appState);

  let fullscreenIsAvailable = $state(fullscreenAvailable());
  let fullscreenIsActive = $state(fullscreenActive());
  let newSessionOpen = $state(false);
  let newSessionSeed = $state<NewSessionSeed | null>(null);
  let newSessionDirectory = $state('');
  let handledNewSessionRequest = $state(0);
  let wakeWanted = $state(false);
  const wakeLock = new WakeLockController((wanted) => {
    wakeWanted = wanted;
  });
  const BUSY_STATUSES = new Set([
    'thinking',
    'working',
    'executing',
    'awaiting_background',
  ]);
  const ESCAPE_GESTURE_MILLISECONDS = 450;
  let escapeTimer: ReturnType<typeof setTimeout> | null = null;
  let lastBusyEscape = 0;

  function readHash(): void {
    appState.readHash(window.location.hash);
  }

  function showList(): void {
    appState.navigate({ kind: 'list' });
  }

  function showStats(): void {
    appState.navigate({ kind: 'stats' });
  }

  function toggleNotifications(): void {
    void appState.setNotificationsEnabled(!appState.notificationsEnabled);
  }

  function openNewSession(workingDirectory = ''): void {
    newSessionDirectory = workingDirectory;
    newSessionSeed = null;
    newSessionOpen = true;
  }

  function requestBrowserNotifications(): void {
    void appState.requestBrowserNotifications();
  }

  function toggleWakeLock(): void {
    void wakeLock.toggle();
  }

  function launch(
    input: LaunchInput,
    display: LaunchDisplay,
    retry: NewSessionSeed,
  ): void {
    newSessionOpen = false;
    void appState.beginLaunch(input, display).catch(() => {
      newSessionSeed = retry;
      newSessionDirectory = retry.workingDirectory;
      newSessionOpen = true;
    });
  }

  async function requestFullscreen(): Promise<void> {
    try {
      await toggleFullscreen();
    } finally {
      fullscreenIsActive = fullscreenActive();
    }
  }

  function readFullscreen(): void {
    fullscreenIsAvailable = fullscreenAvailable();
    fullscreenIsActive = fullscreenActive();
  }

  onMount(() => {
    const controller = new AbortController();
    wakeLock.start();
    void appState.initialize(controller.signal);
    if (startup.openNewSession)
      setTimeout(() => {
        openNewSession();
      }, 0);
    return () => {
      controller.abort();
      if (escapeTimer !== null) clearTimeout(escapeTimer);
      wakeLock.destroy();
      appState.destroy();
    };
  });

  $effect(() => {
    document.body.classList.toggle('in-session', appState.inSession);
    document.body.classList.toggle('fs-on', fullscreenIsActive);
    document.addEventListener('webkitfullscreenchange', readFullscreen);

    return () => {
      document.body.classList.remove('in-session', 'fs-on');
      document.removeEventListener('webkitfullscreenchange', readFullscreen);
    };
  });

  $effect(() => {
    const request = appState.newSessionRequest;
    if (request === null || request.sequence === handledNewSessionRequest)
      return;
    handledNewSessionRequest = request.sequence;
    openNewSession(request.workingDirectory);
  });

  function globalKeydown(event: KeyboardEvent): void {
    if (event.defaultPrevented || handleReadlineKey(event)) return;
    if (
      event.ctrlKey &&
      event.shiftKey &&
      !event.altKey &&
      !event.metaKey &&
      (event.code === 'ArrowLeft' || event.code === 'ArrowRight')
    ) {
      event.preventDefault();
      const current =
        appState.route.kind === 'session' ? appState.route.sessionId : null;
      const next = cycleLiveSession(
        appState.sessions,
        current,
        event.code === 'ArrowRight' ? 1 : -1,
      );
      if (next !== null && next !== current)
        appState.navigate({ kind: 'session', sessionId: next, tab: 'mirror' });
      return;
    }
    if (event.key !== 'Escape' || newSessionOpen) return;
    const view = appState.activeSession;
    if (view?.live !== true) return;
    if (document.querySelector('.nsdropmenu, .rename-menu, .rwmenu') !== null) {
      view.dismissMenus();
      return;
    }
    if (view.rewindPicking) {
      view.setRewindPicking(false);
      return;
    }
    escapeGesture(view);
  }

  function escapeGesture(view: SessionViewState): void {
    const status = view.leadActor?.status ?? '';
    if (status === 'awaiting_attention') {
      clearEscapeTimer();
      appState.showToast(
        'done',
        'a question is waiting',
        'answer it in the card above — Esc would decline it',
      );
      return;
    }
    if (BUSY_STATUSES.has(status)) {
      const now = Date.now();
      if (now - lastBusyEscape < ESCAPE_GESTURE_MILLISECONDS) return;
      lastBusyEscape = now;
      if (view.capabilities?.interrupt === true)
        void interruptFromKeyboard(view);
      return;
    }
    if (escapeTimer !== null) {
      clearEscapeTimer();
      if (view.capabilities?.rewind === true) rewindFromKeyboard(view);
      return;
    }
    escapeTimer = setTimeout(() => {
      escapeTimer = null;
      if (view.capabilities?.interrupt === true)
        void interruptFromKeyboard(view);
    }, ESCAPE_GESTURE_MILLISECONDS);
  }

  function clearEscapeTimer(): void {
    if (escapeTimer === null) return;
    clearTimeout(escapeTimer);
    escapeTimer = null;
  }

  async function interruptFromKeyboard(view: SessionViewState): Promise<void> {
    try {
      const result = await view.interruptTurn();
      showControlFailure('stop failed', result);
    } catch (error) {
      appState.showToast('ask', 'stop failed', errorMessage(error));
    }
  }

  function rewindFromKeyboard(view: SessionViewState): void {
    view.beginRewind();
  }

  function showControlFailure(
    heading: string,
    result: StandardControlOutcome,
  ): void {
    if (result.status === 'acknowledged') return;
    appState.showToast(
      'ask',
      heading,
      result.reason ?? 'the session did not confirm the action',
    );
  }

  function errorMessage(error: unknown): string {
    return error instanceof Error ? error.message : String(error);
  }
</script>

<svelte:head>
  <title>baqylau</title>
</svelte:head>

<svelte:window onhashchange={readHash} />
<svelte:document
  onfullscreenchange={readFullscreen}
  onkeydown={globalKeydown}
/>

<header id="top" inert={newSessionOpen}>
  <a class="brand" href="#/" onclick={showList}>
    <BrandMark />
    <b>baqylau</b>
  </a>
  <div class="topright">
    <div id="sessact" class="topact" hidden={!appState.inSession}>
      {#if appState.inSession}
        {@const activeSession = appState.activeSession}
        {#if activeSession !== null}
          <SessionActions view={activeSession} />
        {/if}
      {/if}
    </div>
    <button
      id="statsbtn"
      class="ghost"
      type="button"
      title="stats"
      onclick={showStats}
    >
      ▦ stats
    </button>
    <button
      id="notifytoggle"
      class={['ghost', { off: !appState.notificationsEnabled }]}
      type="button"
      title={appState.notificationsEnabled
        ? 'All dashboard alerts ON — click to silence every session'
        : 'All dashboard alerts OFF — click to re-enable'}
      disabled={appState.notificationsBusy}
      onclick={toggleNotifications}
    >
      {appState.notificationsEnabled ? '◉ alerts' : '○ alerts off'}
    </button>
    <button
      id="notifbtn"
      class="ghost"
      type="button"
      hidden={appState.browserNotificationPermission !== 'default'}
      onclick={requestBrowserNotifications}>enable notifications</button
    >
    <button
      id="wakebtn"
      class:on={wakeWanted}
      class="ghost"
      type="button"
      title={wakeWanted ? 'screen stays awake' : 'keep screen awake'}
      hidden={!wakeLock.available}
      onclick={toggleWakeLock}
    >
      <SunMark />
    </button>
    <button
      id="fsbtn"
      class="ghost"
      type="button"
      title={fullscreenIsActive ? 'exit fullscreen' : 'fullscreen'}
      hidden={!fullscreenIsAvailable}
      onclick={requestFullscreen}
    >
      ⛶
    </button>
    <span
      id="conn"
      class="dot"
      data-on={appState.connection === 'connected' ? '1' : '0'}
      title="event stream"
    ></span>
    <button
      id="newbtn"
      class="ghost"
      type="button"
      onclick={() => {
        openNewSession();
      }}>+ session</button
    >
  </div>
</header>
<AccountUsageStrip />
<AttentionStrip />
<main id="view" inert={newSessionOpen}>
  {#if appState.route.kind === 'stats'}
    <StatsView />
  {:else if appState.route.kind === 'launching'}
    <LaunchingView {appState} />
  {:else if appState.route.kind === 'session'}
    {#key `${appState.route.sessionId}:${appState.route.actorId ?? ''}`}
      <SessionView route={appState.route} />
    {/key}
  {:else if appState.route.kind === 'not-found'}
    <div class="empty">route not found</div>
  {:else}
    <SessionListView />
  {/if}
</main>
<div id="modal" hidden={!newSessionOpen}>
  {#if newSessionOpen}
    <NewSessionModal
      {appState}
      seed={newSessionSeed}
      prefillWorkingDirectory={newSessionDirectory}
      onclose={() => {
        newSessionOpen = false;
      }}
      onlaunch={launch}
    />
  {/if}
</div>
<ToastStack
  notices={appState.toasts}
  ondismiss={(id: number, action: (() => void) | null) => {
    appState.dismissToast(id, action);
  }}
/>
