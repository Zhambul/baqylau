<script lang="ts">
  import { onDestroy } from 'svelte';

  import {
    autoNameSession,
    background,
    compact,
    renameSession,
    selectEffort,
    selectModel,
  } from '../../api/controls';
  import { getAppState } from '../../app/app-context';
  import type { ControlOutcome } from '../../controls/model';
  import { newRequestId } from '../../shared/browser/identity';
  import type { SessionViewState } from '../session-view-state.svelte';

  type Action =
    | 'compact'
    | 'background'
    | 'model'
    | 'effort'
    | 'rename'
    | 'autoname'
    | 'alerts'
    | 'interrupt'
    | 'close';
  type Menu = 'model' | 'effort' | 'rename';

  const BUSY = new Set([
    'thinking',
    'working',
    'executing',
    'awaiting_background',
  ]);
  const CONFIRM_MS = 4000;
  const CAPABILITY_OFF = "not supported by this session's tool";

  let { view }: { view: SessionViewState } = $props();

  const appState = getAppState();
  let active = $state<Action | null>(null);
  let menu = $state<Menu | null>(null);
  let armed = $state<'compact' | 'close' | null>(null);
  let failure = $state<string | null>(null);
  let renameValue = $state('');
  let confirmTimer: ReturnType<typeof setTimeout> | null = null;
  let handledDismissSequence = $state(0);

  const caps = $derived(view.capabilities);
  const connected = $derived(appState.connection === 'connected');
  const actor = $derived(view.scopedActor);
  const actorScope = $derived(view.actorId !== undefined);
  const busy = $derived(BUSY.has(actor?.status ?? ''));
  const waitingForAttention = $derived(actor?.status === 'awaiting_attention');
  const live = $derived(view.live === true);
  const canStop = $derived(
    actorScope ? actor?.state === 'running' : busy && !waitingForAttention,
  );
  const muted = $derived(
    view.application?.preferences.notificationsMuted ?? false,
  );
  const promptCount = $derived(actor?.statistics.promptCount ?? 0);
  const compactFloor = $derived(
    view.catalog?.commands.find((command) => command.command === 'compact')
      ?.minimumPromptCount ?? null,
  );
  const compactReady = $derived(
    caps?.compact === true &&
      live &&
      !waitingForAttention &&
      compactFloor !== null &&
      promptCount >= compactFloor,
  );
  const modelOptions = $derived(view.catalog?.models ?? []);
  const currentModel = $derived(actor?.model ?? 'model');
  const currentEffort = $derived(actor?.effort ?? 'effort');
  const effortOptions = $derived(
    modelOptions.find(
      (model) =>
        model.modelId === currentModel || model.displayName === currentModel,
    )?.efforts ??
      modelOptions.find((model) => model.default)?.efforts ??
      [],
  );

  $effect(() => {
    const sequence = view.dismissMenusSequence;
    if (sequence === handledDismissSequence) return;
    handledDismissSequence = sequence;
    menu = null;
  });

  onDestroy(() => {
    if (confirmTimer !== null) clearTimeout(confirmTimer);
  });

  function resultFailure(result: ControlOutcome): string | null {
    if (result.status === 'acknowledged') return null;
    return (
      result.reason ??
      (result.status === 'indeterminate'
        ? 'the session did not confirm the action'
        : 'the session rejected the action')
    );
  }

  async function run(
    action: Action,
    operation: () => Promise<ControlOutcome>,
  ): Promise<ControlOutcome | null> {
    if (active !== null) return null;
    active = action;
    failure = null;
    menu = null;
    try {
      const result = await operation();
      failure = resultFailure(result);
      return failure === null ? result : null;
    } catch (error) {
      failure = error instanceof Error ? error.message : String(error);
      return null;
    } finally {
      active = null;
    }
  }

  function arm(action: 'compact' | 'close', operation: () => void): void {
    if (armed !== action) {
      armed = action;
      if (confirmTimer !== null) clearTimeout(confirmTimer);
      confirmTimer = setTimeout(() => {
        armed = null;
        confirmTimer = null;
      }, CONFIRM_MS);
      return;
    }
    armed = null;
    if (confirmTimer !== null) clearTimeout(confirmTimer);
    confirmTimer = null;
    operation();
  }

  async function stop(): Promise<void> {
    await run('interrupt', () => view.interruptTurn());
  }

  function moveToBackground(): void {
    void run('background', () => background(view.sessionId, newRequestId()));
  }

  function requestCompact(): void {
    arm('compact', () => {
      void run('compact', () => compact(view.sessionId, newRequestId()));
    });
  }

  function requestClose(): void {
    arm('close', () => {
      active = 'close';
      failure = null;
      void appState.requestSessionClose(view.sessionId).then((closed) => {
        active = null;
        if (closed) appState.navigate({ kind: 'list' });
        else failure = 'the session could not be closed';
      });
    });
  }

  function startRewind(): void {
    menu = null;
    view.beginRewind();
  }

  function pickModel(modelId: string): void {
    void run('model', () =>
      selectModel(view.sessionId, newRequestId(), modelId),
    );
  }

  function pickEffort(effort: string): void {
    void run('effort', () =>
      selectEffort(view.sessionId, newRequestId(), effort),
    );
  }

  function openRename(): void {
    renameValue = view.session?.title ?? '';
    menu = menu === 'rename' ? null : 'rename';
  }

  function submitRename(): void {
    const name = renameValue.trim();
    if (name.length === 0) return;
    void run('rename', () =>
      renameSession(view.sessionId, newRequestId(), name),
    );
  }

  function submitAutoName(): void {
    void run('autoname', () => autoNameSession(view.sessionId, newRequestId()));
  }
</script>

<div class:hidden={actorScope} class="actrow">
  <span class="qcwrap actses">
    <button
      class="sstop"
      type="button"
      disabled={caps?.model !== true ||
        !connected ||
        !live ||
        waitingForAttention ||
        active !== null}
      title={caps?.model === true ? 'switch the model' : CAPABILITY_OFF}
      onclick={() => (menu = menu === 'model' ? null : 'model')}
      >✦ {currentModel}</button
    >
    {#if menu === 'model'}
      <div class="nsdropmenu qcmenu">
        {#each modelOptions as model (model.modelId)}
          <button
            class="nsdropitem"
            type="button"
            onclick={() => {
              pickModel(model.modelId);
            }}>{model.displayName}</button
          >
        {/each}
      </div>
    {/if}
  </span>
  <span class="qcwrap actses">
    <button
      class="sstop"
      type="button"
      disabled={caps?.effort !== true ||
        !connected ||
        !live ||
        waitingForAttention ||
        active !== null}
      title={caps?.effort === true
        ? 'set the reasoning effort'
        : CAPABILITY_OFF}
      onclick={() => (menu = menu === 'effort' ? null : 'effort')}
      >✧ {currentEffort}</button
    >
    {#if menu === 'effort'}
      <div class="nsdropmenu qcmenu">
        {#each effortOptions as effort (effort.value)}
          <button
            class="nsdropitem"
            type="button"
            onclick={() => {
              pickEffort(effort.value);
            }}>{effort.displayName}</button
          >
        {/each}
      </div>
    {/if}
  </span>
  <button
    class:arm={armed === 'compact'}
    class="sstop actses"
    type="button"
    disabled={!connected || !compactReady || active !== null}
    title={caps?.compact === true ? 'compact the conversation' : CAPABILITY_OFF}
    onclick={requestCompact}
    >{armed === 'compact' ? 'compact now?' : '⊜ compact'}</button
  >
</div>

<div class:hidden={actorScope && !canStop && failure === null} class="actrow">
  <span class:hidden={actorScope} class="qcwrap actses">
    <button
      class="sstop"
      type="button"
      disabled={caps?.rename !== true || !connected || active !== null}
      title={caps?.rename === true ? 'rename this session' : CAPABILITY_OFF}
      onclick={openRename}>✎ rename</button
    >
    {#if menu === 'rename'}
      <form
        class="rename-menu"
        onsubmit={(event) => {
          event.preventDefault();
          submitRename();
        }}
      >
        <input
          bind:value={renameValue}
          maxlength={appState.application?.preferences.limits.renameCharacters}
          aria-label="session name"
        />
        <button type="submit">rename</button>
        <button
          type="button"
          disabled={caps?.autoname !== true}
          title={caps?.autoname === true
            ? 'name this session automatically'
            : CAPABILITY_OFF}
          onclick={submitAutoName}>automatic</button
        >
      </form>
    {/if}
  </span>
  <button
    class:hidden={actorScope}
    class="sstop actses"
    type="button"
    disabled={view.application === null || active !== null}
    title={muted ? 'click to enable alerts' : 'click to mute alerts'}
    onclick={() => {
      active = 'alerts';
      void view.setNotificationsMuted(!muted).finally(() => (active = null));
    }}>{muted ? '○ muted' : '◉ alerts'}</button
  >
  <button
    class:hidden={actorScope}
    class="sstop actses"
    type="button"
    disabled={caps?.rewind !== true ||
      !connected ||
      !live ||
      busy ||
      waitingForAttention ||
      active !== null}
    title={caps?.rewind === true
      ? 'rewind: pick a message to restore to'
      : CAPABILITY_OFF}
    onclick={startRewind}>↶ rewind</button
  >
  <button
    class:hidden={actorScope}
    class="sstop actses"
    type="button"
    disabled={caps?.background !== true ||
      !connected ||
      !live ||
      actor?.status !== 'executing' ||
      active !== null}
    title={caps?.background === true
      ? 'move the foreground command into the background'
      : CAPABILITY_OFF}
    onclick={moveToBackground}>◷ background</button
  >
  <button
    class:hidden={actorScope && !canStop}
    class="sstop actstop"
    type="button"
    disabled={caps?.interrupt !== true ||
      !connected ||
      !live ||
      !canStop ||
      active !== null}
    title={caps?.interrupt === true ? 'stop the turn' : CAPABILITY_OFF}
    onclick={stop}>■ stop</button
  >
  <button
    class:hidden={actorScope}
    class:arm={armed === 'close'}
    class="sstop actses"
    type="button"
    disabled={caps?.close !== true || !connected || !live || active !== null}
    title={caps?.close === true
      ? "close this session's terminal tab"
      : CAPABILITY_OFF}
    onclick={requestClose}
    >{armed === 'close' ? 'close session?' : '✕ close'}</button
  >
  {#if failure !== null}<span class="action-failure" role="alert"
      >{failure}</span
    >{/if}
</div>

<style>
  .nsdropitem {
    display: block;
    width: 100%;
    border: 0;
    background: transparent;
    text-align: left;
  }

  .rename-menu {
    position: absolute;
    z-index: 60;
    top: calc(100% + 4px);
    left: 0;
    display: flex;
    gap: 4px;
    min-width: 320px;
    padding: 6px;
    border-radius: var(--r);
    background: var(--panel);
    box-shadow: var(--card-hover);
  }

  .rename-menu input {
    min-width: 0;
    flex: 1;
    color: var(--text);
    border: 1px solid var(--hair);
    border-radius: var(--r);
    background: var(--bg);
  }

  .rename-menu button {
    color: var(--text-soft);
    border: 0;
    border-radius: var(--r);
    background: var(--panel2);
  }

  .action-failure {
    align-self: center;
    color: var(--red);
    font-size: 10px;
  }

  .hidden {
    display: none;
  }
</style>
