<script lang="ts">
  import { onDestroy, onMount, untrack } from 'svelte';
  import { SvelteSet } from 'svelte/reactivity';

  import {
    readLaunchCatalog,
    saveNewSessionDraft,
    saveNewSessionPreferences,
  } from '../api/new-session';
  import type { AppState } from '../app/app-state.svelte';
  import type { SessionId } from '../app/domain-ids';
  import { readSessionDirectories } from '../api/session-data';
  import AttachmentButton from '../attachments/AttachmentButton.svelte';
  import AttachmentStrip from '../attachments/AttachmentStrip.svelte';
  import {
    AttachmentTrayState,
    filesFromClipboard,
    hasDraggedFiles,
  } from '../attachments/attachment-tray.svelte';
  import SlashCommandMenu from '../commands/SlashCommandMenu.svelte';
  import DictationButton from '../dictation/DictationButton.svelte';
  import type { HarnessCatalog } from '../harnesses/model';
  import { autoGrow } from '../shared/browser/auto-grow';
  import { isIPad } from '../shared/browser/device';
  import type {
    LaunchDisplay,
    LaunchInput,
    NewSessionSeed,
    ResumableSession,
  } from './model';
  import CustomSelect from './CustomSelect.svelte';
  import ResumePicker from './ResumePicker.svelte';

  const DRAFT_DELAY_MS = 350;

  let {
    appState,
    seed,
    prefillWorkingDirectory,
    onclose,
    onlaunch,
  }: {
    appState: AppState;
    seed: NewSessionSeed | null;
    prefillWorkingDirectory: string;
    onclose: () => void;
    onlaunch: (
      input: LaunchInput,
      display: LaunchDisplay,
      retry: NewSessionSeed,
    ) => void;
  } = $props();

  const ipad = isIPad();

  const initial = untrack(() => {
    const preferredDirectory =
      prefillWorkingDirectory.length > 0
        ? prefillWorkingDirectory
        : (appState.application?.preferences.newSession.workingDirectory ?? '');
    const directory = seed?.workingDirectory ?? preferredDirectory;
    return {
      directory,
      harness:
        seed?.harness ??
        appState.application?.preferences.newSession.harness ??
        '',
      modelId:
        seed?.modelId ??
        appState.application?.preferences.newSession.model ??
        '',
      effort:
        seed?.effort ??
        appState.application?.preferences.newSession.effort ??
        '',
      accountId: seed?.accountId ?? '',
      prompt: seed?.prompt ?? savedDraft(directory),
      draftEdited: seed?.prompt !== undefined,
      fresh: (seed?.resumeSessionId ?? null) === null,
      resumeSessionId: seed?.resumeSessionId ?? null,
      attachments: seed?.attachments ?? [],
    };
  });

  const launchableHarnesses = $derived(
    appState.harnesses.filter((harness) => harness.launchable),
  );
  let directoryChoices = $state<readonly string[]>([]);
  let directoryQuery = $state('');
  const matchingDirectories = $derived(
    directoryChoices.filter(
      (choice) =>
        !appState.hiddenDirectories.has(choice) &&
        choice.toLowerCase().includes(directoryQuery.toLowerCase()),
    ),
  );

  let panel = $state<HTMLElement>();
  let promptBox = $state<HTMLTextAreaElement>();
  let promptHost = $state<HTMLElement>();
  let slashMenu = $state<{ handleKey: (event: KeyboardEvent) => boolean }>();
  let dictation = $state<{ stop: () => void }>();
  const attachmentTray = new AttachmentTrayState(initial.attachments);
  let workingDirectory = $state(initial.directory);
  let draftDirectory = $state(initial.directory);
  let harness = $state(initial.harness);
  let modelId = $state(initial.modelId);
  let effort = $state(initial.effort);
  let preserveNativeEffort = $state(
    !initial.fresh && initial.effort.length === 0,
  );
  let accountId = $state(initial.accountId);
  let prompt = $state(initial.prompt);
  let draftEdited = $state(initial.draftEdited);
  let fresh = $state(initial.fresh);
  let resumeSessionId = $state<SessionId | null>(initial.resumeSessionId);
  let catalog = $state<HarnessCatalog | null>(null);
  let catalogFailure = $state<string | null>(null);
  let catalogLoading = $state(false);
  let directoryMenu = $state(false);
  let submitting = $state(false);
  let failure = $state<string | null>(null);
  let dropping = $state(false);
  let draftTimer: ReturnType<typeof setTimeout> | null = null;
  let catalogRequest: AbortController | null = null;

  const selectedHarness = $derived(
    launchableHarnesses.find((item) => item.name === harness) ?? null,
  );
  const selectedModel = $derived(
    catalog?.models.find((model) => model.modelId === modelId) ?? null,
  );
  const harnessOptions = $derived(
    launchableHarnesses.map((item) => ({
      value: item.name,
      label: item.displayName,
    })),
  );
  const modelOptions = $derived(
    (catalog?.models ?? []).map((model) => ({
      value: model.modelId,
      label: model.displayName,
    })),
  );
  const effortOptions = $derived(
    (selectedModel?.efforts ?? []).map((item) => ({
      value: item.value,
      label: item.displayName,
    })),
  );
  const accountOptions = $derived.by(() => {
    const options = [{ value: '', label: 'automatic' }];
    if (selectedHarness?.supportsAccounts !== true) return options;
    const seen = new SvelteSet<string>();
    for (const row of appState.application?.usageRows ?? []) {
      if (
        row.harness !== harness ||
        row.accountId === null ||
        seen.has(row.accountId) ||
        !row.schedulingAllowed
      )
        continue;
      seen.add(row.accountId);
      options.push({ value: row.accountId, label: row.displayName });
    }
    return options;
  });

  $effect(() => {
    const saved = appState.application?.preferences.newSessionDrafts.find(
      (draft) => draft.workingDirectory === draftDirectory,
    );
    if (!draftEdited && saved !== undefined) prompt = saved.text;
  });

  $effect(() => {
    if (harness.length > 0 || launchableHarnesses.length === 0) return;
    const preferred =
      launchableHarnesses.find((item) => item.defaultForLaunch) ??
      launchableHarnesses[0];
    if (preferred !== undefined) harness = preferred.name;
  });

  $effect(() => {
    const selectedHarness = harness;
    const directory = workingDirectory;
    if (selectedHarness.length === 0) return;
    void loadCatalog(selectedHarness, directory);
  });

  $effect(() => {
    if (catalog === null) return;
    const models = catalog.models;
    if (models.length === 0) {
      modelId = '';
      effort = '';
      return;
    }
    if (!models.some((model) => model.modelId === modelId))
      modelId =
        (models.find((model) => model.default) ?? models[0])?.modelId ?? '';
  });

  $effect(() => {
    if (catalog === null) return;
    const efforts = selectedModel?.efforts ?? [];
    if (efforts.length === 0) {
      effort = '';
      return;
    }
    if (!fresh && preserveNativeEffort && effort.length === 0) return;
    if (!efforts.some((item) => item.value === effort))
      effort =
        (efforts.find((item) => item.default) ?? efforts[0])?.value ?? '';
  });

  onMount(() => {
    document.body.classList.add('modal-open');
    const controller = new AbortController();
    void readSessionDirectories(controller.signal)
      .then((directories) => {
        directoryChoices = directories;
      })
      .catch(() => undefined);
    return () => {
      controller.abort();
      document.body.classList.remove('modal-open');
    };
  });

  onDestroy(() => {
    if (draftTimer !== null) clearTimeout(draftTimer);
    catalogRequest?.abort();
    attachmentTray.clear();
  });

  function savedDraft(directory: string): string {
    return (
      appState.application?.preferences.newSessionDrafts.find(
        (draft) => draft.workingDirectory === directory,
      )?.text ?? ''
    );
  }

  async function loadCatalog(
    selectedHarness: string,
    directory: string,
  ): Promise<void> {
    catalogRequest?.abort();
    const controller = new AbortController();
    catalogRequest = controller;
    catalogLoading = true;
    catalogFailure = null;
    // Do not let a previous harness's catalog rewrite a resume row's saved
    // model while the matching catalog is in flight.
    catalog = null;
    try {
      const result = await readLaunchCatalog(
        selectedHarness,
        directory.trim(),
        controller.signal,
      );
      if (catalogRequest !== controller) return;
      catalog = result;
    } catch (error) {
      if (controller.signal.aborted) return;
      catalog = null;
      catalogFailure = error instanceof Error ? error.message : String(error);
    } finally {
      if (catalogRequest === controller) {
        catalogRequest = null;
        catalogLoading = false;
      }
    }
  }

  function dispatchDraft(directory: string, text: string): void {
    void saveNewSessionDraft(directory, text, Date.now()).catch(
      (error: unknown) => {
        failure = error instanceof Error ? error.message : String(error);
      },
    );
  }

  function scheduleDraft(): void {
    draftEdited = true;
    if (draftTimer !== null) clearTimeout(draftTimer);
    const directory = draftDirectory;
    const text = prompt;
    draftTimer = setTimeout(() => {
      draftTimer = null;
      dispatchDraft(directory, text);
    }, DRAFT_DELAY_MS);
  }

  function settleDirectory(): void {
    directoryMenu = false;
    const next = workingDirectory.trim();
    if (next === draftDirectory) return;
    const carried = prompt;
    dispatchDraft(draftDirectory, carried);
    draftDirectory = next;
    const saved = savedDraft(next);
    if (saved.length > 0) {
      prompt = saved;
      draftEdited = false;
    } else if (carried.trim().length > 0) {
      draftEdited = true;
      dispatchDraft(next, carried);
    } else {
      draftEdited = false;
    }
  }

  function selectResume(row: ResumableSession): void {
    if (launchableHarnesses.some((item) => item.name === row.harness))
      harness = row.harness;
    modelId = row.model?.id ?? modelId;
    preserveNativeEffort = row.effort === null;
    effort = row.effort ?? '';
    accountId = row.account?.id ?? accountId;
  }

  function trapKeys(event: KeyboardEvent): void {
    if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      close();
      return;
    }
    if (event.key !== 'Tab' || panel === undefined) return;
    const focusable = [
      ...panel.querySelectorAll<HTMLElement>(
        'button:not(:disabled), input:not(:disabled), textarea:not(:disabled), [tabindex="0"]',
      ),
    ].filter((element) => !element.hidden && element.offsetParent !== null);
    if (focusable.length === 0) return;
    const first = focusable[0];
    const last = focusable.at(-1);
    if (first === undefined || last === undefined) return;
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  function close(): void {
    dictation?.stop();
    if (draftTimer !== null) {
      clearTimeout(draftTimer);
      draftTimer = null;
    }
    dispatchDraft(draftDirectory, prompt.trim().length > 0 ? prompt : '');
    onclose();
  }

  function paste(event: ClipboardEvent): void {
    if (
      selectedHarness?.supportsAttachments !== true ||
      promptBox === undefined
    )
      return;
    const files = filesFromClipboard(event);
    if (files.length === 0) return;
    event.preventDefault();
    void attachmentTray.pasteFiles(
      files,
      null,
      appState.application?.preferences.limits.uploadBytes ?? null,
      promptBox,
    );
  }

  function dragover(event: DragEvent): void {
    if (
      selectedHarness?.supportsAttachments !== true ||
      !hasDraggedFiles(event)
    )
      return;
    event.preventDefault();
    dropping = true;
  }

  function drop(event: DragEvent): void {
    dropping = false;
    if (
      selectedHarness?.supportsAttachments !== true ||
      event.dataTransfer === null ||
      event.dataTransfer.files.length === 0
    )
      return;
    event.preventDefault();
    void attachmentTray.addFiles(
      event.dataTransfer.files,
      null,
      appState.application?.preferences.limits.uploadBytes ?? null,
    );
  }

  function pickFiles(files: FileList): void {
    void attachmentTray.addFiles(
      files,
      null,
      appState.application?.preferences.limits.uploadBytes ?? null,
    );
  }

  function dictationFailure(message: string): void {
    failure = message;
  }

  function dictationTelemetry(
    name: string,
    details: Readonly<Record<string, string | number | boolean | null>>,
  ): void {
    appState.audit.record(null, name, details);
  }

  function submit(): void {
    dictation?.stop();
    const directory = workingDirectory.trim();
    const currentHarness = selectedHarness;
    if (submitting) return;
    failure = null;
    if (directory.length === 0) {
      failure = 'choose a working directory';
      return;
    }
    if (currentHarness === null || catalog === null) {
      failure = 'the harness catalog is not loaded yet';
      return;
    }
    if (!fresh && resumeSessionId === null) {
      failure = 'choose a conversation to resume, or switch to fresh';
      return;
    }
    if (attachmentTray.pending) {
      failure = 'attachment still uploading; one moment…';
      return;
    }
    const attachments = currentHarness.supportsAttachments
      ? attachmentTray.attachments
      : [];
    if (
      currentHarness.requiresInitialMessage &&
      prompt.trim().length === 0 &&
      attachments.length === 0
    ) {
      failure = `${currentHarness.displayName} needs a first message`;
      return;
    }
    const input: LaunchInput = {
      harness: currentHarness.name,
      workingDirectory: directory,
      initialText: prompt.trim().length > 0 ? prompt.trim() : null,
      modelId: modelId.length > 0 ? modelId : null,
      effort: effort.length > 0 ? effort : null,
      accountId:
        currentHarness.supportsAccounts && accountId.length > 0
          ? accountId
          : null,
      resumeSessionId: fresh ? null : resumeSessionId,
      attachments,
    };
    const display: LaunchDisplay = {
      mode: input.resumeSessionId === null ? 'new' : 'resume',
      toolLabel: currentHarness.displayName,
      model: selectedModel?.displayName ?? '',
      effort,
      account:
        accountOptions.find((option) => option.value === accountId)?.label ??
        '',
      prompt: input.initialText ?? '',
    };
    const retry: NewSessionSeed = {
      workingDirectory: directory,
      harness: currentHarness.name,
      modelId,
      effort,
      accountId,
      prompt,
      resumeSessionId: input.resumeSessionId,
      attachments,
    };
    submitting = true;
    if (draftTimer !== null) {
      clearTimeout(draftTimer);
      draftTimer = null;
    }
    dispatchDraft(directory, '');
    void saveNewSessionPreferences(
      directory,
      harness,
      input.modelId,
      input.effort,
    );
    onlaunch(input, display, retry);
  }
</script>

<svelte:window onkeydown={trapKeys} />

<div
  class="nsback"
  role="presentation"
  onclick={(event) => {
    if (event.target === event.currentTarget) close();
  }}
>
  <div
    bind:this={panel}
    class="nspanel"
    role="dialog"
    aria-modal="true"
    aria-labelledby="new-session-title"
  >
    <div id="new-session-title" class="nstitle">new session</div>
    <div class="nsfield">
      <label class="nslabel" for="new-session-directory">directory</label>
      <input
        id="new-session-directory"
        bind:value={workingDirectory}
        class="nsinput"
        type="text"
        autocomplete="off"
        autocapitalize="none"
        spellcheck="false"
        placeholder="/path/to/project"
        onfocus={() => {
          directoryQuery = '';
          directoryMenu = true;
        }}
        oninput={() => {
          directoryQuery = workingDirectory;
          directoryMenu = true;
        }}
        onblur={settleDirectory}
      />
      {#if directoryMenu && matchingDirectories.length > 0}
        <div class="nsdropmenu" role="listbox" aria-label="known directories">
          {#each matchingDirectories as directory (directory)}
            <button
              class="nsdropitem"
              role="option"
              aria-selected={directory === workingDirectory}
              type="button"
              onmousedown={(event) => {
                event.preventDefault();
              }}
              onclick={() => {
                workingDirectory = directory;
                settleDirectory();
              }}>{directory}</button
            >
          {/each}
        </div>
      {/if}
    </div>

    <div class="nsfield">
      <span class="nslabel">harness</span>
      <CustomSelect
        bind:value={harness}
        options={harnessOptions}
        label="harness"
        disabled={appState.harnessState === 'loading'}
      />
    </div>

    <div class="nsfield">
      <span class="nslabel">start</span>
      <label class="nsswitch">
        <input bind:checked={fresh} type="checkbox" />
        <span class="nsslider"></span>
        <span class="nsswitchtxt"
          >{fresh ? 'fresh conversation' : 'resume a conversation'}</span
        >
      </label>
    </div>

    {#if !fresh}
      <div class="nsfield nsresumerow">
        <span class="nslabel">resume</span>
        <ResumePicker
          bind:value={resumeSessionId}
          {workingDirectory}
          harnesses={appState.harnesses}
          onselect={selectResume}
        />
      </div>
    {/if}

    {#if selectedHarness?.supportsAccounts}
      <div class="nssplit">
        <div class="nsfield">
          <span class="nslabel">account</span>
          <CustomSelect
            bind:value={accountId}
            options={accountOptions}
            label="account"
          />
        </div>
      </div>
    {/if}

    <div class="nssplit">
      <div class="nsfield">
        <span class="nslabel">model</span>
        <CustomSelect
          bind:value={modelId}
          options={modelOptions}
          label="model"
          disabled={catalogLoading || modelOptions.length === 0}
        />
      </div>
      <div class="nsfield">
        <span class="nslabel">effort</span>
        <CustomSelect
          bind:value={effort}
          options={effortOptions}
          label="effort"
          disabled={catalogLoading || effortOptions.length === 0}
        />
      </div>
    </div>

    <label
      bind:this={promptHost}
      class:dropping
      class="nsfield"
      ondragover={dragover}
      ondragleave={(event) => {
        if (event.target === event.currentTarget) dropping = false;
      }}
      ondrop={drop}
    >
      <span class="nslabel"
        >first prompt ({selectedHarness?.requiresInitialMessage
          ? 'required'
          : 'optional'})</span
      >
      {#if selectedHarness?.supportsAttachments === true}
        <AttachmentStrip tray={attachmentTray} />
      {/if}
      <div class="nsdictrow">
        <textarea
          bind:this={promptBox}
          bind:value={prompt}
          use:autoGrow={prompt}
          class="nsinput nsprompt"
          rows="3"
          spellcheck="false"
          placeholder={ipad
            ? `what should ${selectedHarness?.displayName ?? 'the agent'} start on?`
            : `what should ${selectedHarness?.displayName ?? 'the agent'} start on?  (Enter to launch · Shift+Enter for newline)`}
          oninput={scheduleDraft}
          onpaste={paste}
          onkeydown={(event) => {
            if (slashMenu?.handleKey(event) === true) return;
            if (
              !ipad &&
              event.key === 'Enter' &&
              !event.shiftKey &&
              !event.isComposing
            ) {
              event.preventDefault();
              submit();
            }
          }}></textarea>
        {#if selectedHarness?.supportsAttachments === true}
          <AttachmentButton disabled={submitting} onpick={pickFiles} />
        {/if}
        <DictationButton
          bind:this={dictation}
          textarea={promptBox}
          {harness}
          {workingDirectory}
          sessionId={null}
          disabled={submitting}
          onfailure={dictationFailure}
          ontelemetry={dictationTelemetry}
        />
      </div>
      <SlashCommandMenu
        bind:this={slashMenu}
        bind:value={prompt}
        commands={catalog?.commands ?? []}
        textarea={promptBox}
        host={promptHost}
        enterSends={!ipad}
        onneedcommands={() => {
          if (!catalogLoading && harness.length > 0)
            void loadCatalog(harness, workingDirectory);
        }}
      />
    </label>

    {#if catalogFailure !== null}
      <div class="nsresempty" role="alert">{catalogFailure}</div>
    {/if}
    {#if failure !== null || attachmentTray.failure !== null}
      <div class="nsresempty" role="alert">
        {attachmentTray.failure ?? failure}
      </div>
    {/if}
    <div class="nsactions">
      <button class="nsbtn" type="button" onclick={close}>cancel</button>
      <button
        class="nsbtn primary"
        type="button"
        disabled={submitting || catalogLoading || catalog === null}
        onclick={submit}>{submitting ? 'launching…' : 'launch'}</button
      >
    </div>
  </div>
</div>

<style>
  .nsfield > .nsdropmenu {
    top: calc(100% - 8px);
  }

  button.nsdropitem {
    display: block;
    width: 100%;
    border: 0;
    background: transparent;
    text-align: left;
  }
</style>
