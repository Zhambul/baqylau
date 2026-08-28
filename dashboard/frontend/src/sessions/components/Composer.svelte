<script lang="ts">
  import { onDestroy } from 'svelte';

  import { sendText } from '../../api/controls';
  import { saveComposerDraft } from '../../api/session-preferences';
  import { getAppState } from '../../app/app-context';
  import AttachmentButton from '../../attachments/AttachmentButton.svelte';
  import AttachmentStrip from '../../attachments/AttachmentStrip.svelte';
  import {
    AttachmentTrayState,
    filesFromClipboard,
    hasDraggedFiles,
  } from '../../attachments/attachment-tray.svelte';
  import SlashCommandMenu from '../../commands/SlashCommandMenu.svelte';
  import type { ControlOutcome } from '../../controls/model';
  import DictationButton from '../../dictation/DictationButton.svelte';
  import type { Entry } from '../../entries/model';
  import type { LaunchDisplay, LaunchInput } from '../../new-session/model';
  import { autoGrow } from '../../shared/browser/auto-grow';
  import { isIPad } from '../../shared/browser/device';
  import { newRequestId } from '../../shared/browser/identity';
  import { reportClientFailure } from '../../shared/browser/optimistic-action';
  import type { SessionViewState } from '../session-view-state.svelte';

  const DRAFT_DELAY_MS = 350;
  let { view }: { view: SessionViewState } = $props();

  const appState = getAppState();
  const ipad = isIPad();
  const attachmentTray = new AttachmentTrayState();
  let textarea = $state<HTMLTextAreaElement>();
  let composer = $state<HTMLElement>();
  let slashMenu = $state<{ handleKey: (event: KeyboardEvent) => boolean }>();
  let dictation = $state<{ stop: () => void }>();
  let draft = $state('');
  let seeded = $state(false);
  let edited = false;
  let sending = $state(false);
  let failure = $state<string | null>(null);
  let dropping = $state(false);
  let historyIndex = $state<number | null>(null);
  let historyBase = $state('');
  let mounted = true;
  let timer: ReturnType<typeof setTimeout> | null = null;

  window.addEventListener('pagehide', persistDraft);

  const canSend = $derived(
    appState.connection === 'connected' &&
      view.live === true &&
      view.capabilities?.send === true,
  );
  const canResume = $derived(
    appState.connection === 'connected' &&
      view.live === false &&
      (view.session?.workingDirectory.trim().length ?? 0) > 0,
  );
  const usable = $derived(canSend || canResume);
  const suggestion = $derived(
    view.application?.terminal.inputState?.suggestion ?? '',
  );
  const placeholder = $derived(
    suggestion.length > 0 && draft.length === 0
      ? suggestion
      : canSend
        ? ipad
          ? 'message this session…'
          : 'message this session…  (Enter to send · Shift+Enter for newline)'
        : canResume
          ? ipad
            ? 'message this parked session — sending resumes it'
            : 'message this parked session — sending resumes it  (Enter to resume & send)'
          : appState.connection !== 'connected'
            ? 'dashboard is reconnecting…'
            : view.capabilities?.send === false
              ? "this session's tool can't be messaged from here"
              : 'session is not live',
  );
  const promptHistory = $derived(
    view.entries
      .filter(isDeliveredPrompt)
      .map((entry) => entry.body.content.text),
  );

  $effect(() => {
    const application = view.application;
    if (seeded || application === null) return;
    const saved = application.composer.draft;
    if (!edited && saved !== null) draft = saved.text;
    seeded = true;
  });

  $effect(() => {
    const override = view.composerOverride;
    if (override === null) return;
    edited = true;
    draft = override.text;
    view.consumeComposerOverride(override.sequence);
    requestAnimationFrame(() => {
      textarea?.focus();
    });
  });

  onDestroy(() => {
    mounted = false;
    window.removeEventListener('pagehide', persistDraft);
    flushDraft();
    attachmentTray.clear();
  });

  function dispatchDraft(text: string): void {
    void saveComposerDraft(
      view.sessionId,
      text,
      view.clientId,
      Date.now(),
    ).catch((error: unknown) => {
      failure = error instanceof Error ? error.message : String(error);
    });
  }

  function flushDraft(): void {
    if (timer === null) return;
    clearTimeout(timer);
    timer = null;
    dispatchDraft(draft);
  }

  function persistDraft(): void {
    flushDraft();
  }

  function scheduleDraft(): void {
    if (timer !== null) clearTimeout(timer);
    timer = setTimeout(() => {
      timer = null;
      dispatchDraft(draft);
    }, DRAFT_DELAY_MS);
  }

  function input(): void {
    edited = true;
    failure = null;
    historyIndex = null;
    scheduleDraft();
  }

  function outcomeFailure(outcome: ControlOutcome): string | null {
    if (outcome.kind === 'message-delivery') return null;
    if (outcome.status === 'acknowledged') return null;
    if (outcome.reason !== null && outcome.reason.length > 0)
      return outcome.reason;
    return outcome.status === 'indeterminate'
      ? 'the session did not confirm the message'
      : 'the session rejected the message';
  }

  async function submit(): Promise<void> {
    dictation?.stop();
    const text = draft.trim();
    const attachments = attachmentTray.attachments;
    if (
      sending ||
      !usable ||
      (text.trim().length === 0 && attachments.length === 0)
    )
      return;
    if (attachmentTray.pending) {
      failure = 'attachment still uploading; one moment…';
      return;
    }
    sending = true;
    failure = null;
    if (canResume) {
      await resume(text, attachments);
      return;
    }
    const requestId = newRequestId();
    view.showPendingPrompt(requestId, text);
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
    // This immediate clear has a later dispatch timestamp than every pending save.
    dispatchDraft('');
    try {
      const outcome = await sendText(
        view.sessionId,
        requestId,
        text,
        attachments,
        false,
      );
      const rejected = outcomeFailure(outcome);
      if (rejected !== null) {
        view.settlePendingPrompt(requestId, 'dropped', outcome.status);
        failure = rejected;
        dispatchDraft(text);
        return;
      }
      if (outcome.kind === 'message-delivery' && outcome.status === 'queued') {
        view.settlePendingPrompt(requestId, 'dropped', 'queued');
        view.queuePendingPrompt(requestId, text);
      }
      draft = '';
      attachmentTray.clear();
    } catch (error) {
      view.settlePendingPrompt(requestId, 'dropped', 'send-failed');
      reportClientFailure(view.sessionId, 'send', error, text.length);
      failure = error instanceof Error ? error.message : String(error);
      dispatchDraft(text);
    } finally {
      sending = false;
      if (!ipad) textarea?.focus();
    }
  }

  async function resume(
    text: string,
    attachments: LaunchInput['attachments'],
  ): Promise<void> {
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
    dispatchDraft('');
    const session = view.session;
    if (session === null) {
      sending = false;
      failure = 'session facts are unavailable';
      dispatchDraft(text);
      return;
    }
    const model = view.catalog?.models.find(
      (option) =>
        option.modelId === view.scopedActor?.model ||
        option.displayName === view.scopedActor?.model,
    );
    const input: LaunchInput = {
      harness: session.harness,
      workingDirectory: session.workingDirectory,
      initialText: text.length > 0 ? text : null,
      modelId: model?.modelId ?? null,
      effort: view.scopedActor?.effort ?? null,
      accountId: session.account?.accountId ?? null,
      resumeSessionId: session.sessionId,
      attachments,
    };
    const display: LaunchDisplay = {
      mode: 'resume',
      toolLabel: view.harness?.displayName ?? session.harness,
      model: model?.displayName ?? view.scopedActor?.model ?? '',
      effort: input.effort ?? '',
      account: session.account?.displayName ?? '',
      prompt: text,
    };
    try {
      await appState.beginComposerResume(input, display, () => {
        if (!mounted) return;
        sending = false;
        failure =
          'the session never came back — your message is kept; try again';
        dispatchDraft(text);
        appState.showToast('ask', 'resume timed out', failure);
      });
      attachmentTray.clear();
      appState.showToast(
        'done',
        'resuming session',
        'your message starts the revived turn',
      );
    } catch (error) {
      sending = false;
      failure = error instanceof Error ? error.message : String(error);
      reportClientFailure(view.sessionId, 'resume', error, text.length);
      dispatchDraft(text);
      appState.showToast('ask', 'resume failed', failure);
      if (!ipad) textarea?.focus();
    }
  }

  function paste(event: ClipboardEvent): void {
    if (!usable || textarea === undefined) return;
    const files = filesFromClipboard(event);
    if (files.length === 0) return;
    event.preventDefault();
    void attachmentTray.pasteFiles(
      files,
      view.sessionId,
      view.uploadLimit,
      textarea,
    );
  }

  function dragover(event: DragEvent): void {
    if (!usable || !hasDraggedFiles(event)) return;
    event.preventDefault();
    dropping = true;
  }

  function drop(event: DragEvent): void {
    dropping = false;
    if (!usable || event.dataTransfer === null) return;
    const files = event.dataTransfer.files;
    if (files.length === 0) return;
    event.preventDefault();
    void attachmentTray.addFiles(files, view.sessionId, view.uploadLimit);
  }

  function pickFiles(files: FileList): void {
    void attachmentTray.addFiles(files, view.sessionId, view.uploadLimit);
  }

  function dictationFailure(message: string): void {
    failure = message;
  }

  function dictationTelemetry(
    name: string,
    details: Readonly<Record<string, string | number | boolean | null>>,
  ): void {
    view.recordBrowserEvent(name, details);
  }

  function keydown(event: KeyboardEvent): void {
    if (slashMenu?.handleKey(event) === true) return;
    if (
      (event.key === 'ArrowRight' || event.key === 'Tab') &&
      draft.length === 0 &&
      suggestion.length > 0
    ) {
      event.preventDefault();
      acceptSuggestion();
      return;
    }
    if (
      (event.key === 'ArrowUp' || event.key === 'ArrowDown') &&
      recallHistory(event.key === 'ArrowUp')
    ) {
      event.preventDefault();
      return;
    }
    if (
      !ipad &&
      event.key === 'Enter' &&
      !event.shiftKey &&
      !event.isComposing &&
      !event.altKey &&
      !event.ctrlKey &&
      !event.metaKey
    ) {
      event.preventDefault();
      void submit();
    }
  }

  function acceptSuggestion(): void {
    if (draft.length > 0 || suggestion.length === 0) return;
    draft = suggestion;
    input();
    requestAnimationFrame(() => {
      textarea?.setSelectionRange(draft.length, draft.length);
    });
  }

  function recallHistory(up: boolean): boolean {
    if (textarea === undefined) return false;
    const inputElement = textarea;
    const navigating = historyIndex !== null;
    if (!navigating) {
      if (!up) return false;
      if (textarea.selectionStart !== 0 || textarea.selectionEnd !== 0)
        return false;
    }
    if (promptHistory.length === 0) return navigating;
    let next = historyIndex;
    if (next === null) {
      historyBase = draft;
      next = -1;
    }
    next += up ? 1 : -1;
    if (next >= promptHistory.length) next = promptHistory.length - 1;
    let auditIndex: number | 'draft';
    if (next < 0) {
      historyIndex = null;
      draft = historyBase;
      auditIndex = 'draft';
    } else {
      historyIndex = next;
      draft = promptHistory[next] ?? '';
      auditIndex = next;
    }
    requestAnimationFrame(() => {
      inputElement.setSelectionRange(draft.length, draft.length);
    });
    view.recordBrowserEvent('composer.recall', {
      dir: up ? 'up' : 'down',
      idx: auditIndex,
      n: promptHistory.length,
    });
    return true;
  }

  function isDeliveredPrompt(
    entry: Entry,
  ): entry is Extract<Entry, { readonly type: 'message' }> {
    return (
      entry.type === 'message' &&
      entry.body.role === 'user' &&
      entry.body.phase === 'prompt' &&
      entry.body.content.text.length > 0
    );
  }
</script>

{#if view.queuedPromptTexts.length > 0}
  <div class="pinq">
    {#each view.queuedPromptTexts as text, index (`${text}:${String(index)}`)}
      <div class="msg prompt queued">
        <span class="who">you <span class="qbadge">⧗ queued</span></span>
        <div class="md"><p>{text}</p></div>
      </div>
    {/each}
  </div>
{/if}

<div
  bind:this={composer}
  class:dropping
  class="composer"
  role="group"
  aria-label="message composer"
  ondragover={dragover}
  ondragleave={(event) => {
    if (event.target === event.currentTarget) dropping = false;
  }}
  ondrop={drop}
>
  {#if view.harness?.supportsAttachments === true}
    <AttachmentStrip tray={attachmentTray} />
  {/if}
  <textarea
    bind:this={textarea}
    bind:value={draft}
    use:autoGrow={draft}
    class:hasghost={suggestion.length > 0 && draft.length === 0}
    class="cinput"
    rows="1"
    spellcheck="false"
    disabled={!usable || sending}
    {placeholder}
    oninput={input}
    onpaste={paste}
    onkeydown={keydown}></textarea>
  {#if view.harness?.supportsAttachments === true}
    <AttachmentButton disabled={!usable || sending} onpick={pickFiles} />
  {/if}
  <DictationButton
    bind:this={dictation}
    {textarea}
    harness={view.session?.harness ?? ''}
    workingDirectory={view.session?.workingDirectory ?? ''}
    sessionId={view.sessionId}
    disabled={!usable || sending}
    onfailure={dictationFailure}
    ontelemetry={dictationTelemetry}
  />
  {#if ipad && suggestion.length > 0 && draft.length === 0}
    <button class="chint" type="button" onclick={acceptSuggestion}
      >use hint</button
    >
  {/if}
  <button
    class="csend"
    type="button"
    disabled={!usable ||
      sending ||
      (draft.trim().length === 0 && attachmentTray.attachments.length === 0)}
    onclick={submit}
  >
    {sending
      ? canResume
        ? 'resuming…'
        : 'sending…'
      : canResume
        ? 'resume & send'
        : 'send'}
  </button>
  {#if failure !== null || attachmentTray.failure !== null}
    <div class="composer-error" role="alert">
      {attachmentTray.failure ?? failure}
    </div>
  {/if}
  <SlashCommandMenu
    bind:this={slashMenu}
    bind:value={draft}
    commands={view.catalog?.commands ?? []}
    {textarea}
    host={composer}
    enterSends={!ipad}
    onneedcommands={() => {
      void view.retryCatalog();
    }}
  />
</div>

<style>
  .composer-error {
    flex-basis: 100%;
    color: var(--red);
    font-size: 11px;
  }
</style>
