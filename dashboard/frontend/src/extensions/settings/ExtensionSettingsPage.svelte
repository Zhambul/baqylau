<script lang="ts">
  import { HttpFailure, messageFrom } from '../../api/client';
  import {
    readExtensionSettings,
    saveExtensionSettings,
    StaleSettingsError,
    type ExtensionOperation,
    type ExtensionSettings,
    type SettingsScope,
  } from '../../api/extension-settings';
  import { readExtensionOperation } from '../../api/extensions';
  import type { ExtensionSettingsRoute } from '../../app/route';
  import { formatRoute } from '../../app/route';
  import ExtensionViewMount from '../views/ExtensionViewMount.svelte';
  import { webViewCatalog } from '../views/web-view-catalog.svelte';
  import SecretControls from './SecretControls.svelte';
  import SettingsForm from './SettingsForm.svelte';
  import {
    effectiveValue,
    scopeLabel,
    SETTINGS_EFFECT,
    settingsDocument,
    settingsSchema,
  } from './settings-model';

  const OPERATION_POLL_MS = 1_000;
  const CONFLICT = 409;

  let { route }: { route: ExtensionSettingsRoute } = $props();

  const scope: SettingsScope = $derived(
    route.workspaceId === undefined
      ? { kind: 'installation' }
      : { kind: 'workspace', workspace_id: route.workspaceId },
  );
  const catalog = webViewCatalog();
  const panels = $derived(
    (catalog?.forSlot('settings', scope.kind) ?? []).filter(
      (view) => view.extension_id === route.extensionId,
    ),
  );

  let reply = $state<ExtensionSettings | null>(null);
  let loadFailure = $state<string | null>(null);
  let operation = $state<ExtensionOperation | null>(null);
  let writeFailure = $state<string | null>(null);
  let stale = $state(false);
  let page = new AbortController();

  const snapshot = $derived(reply?.settings ?? null);
  const schema = $derived(snapshot === null ? null : settingsSchema(snapshot));
  const declared = $derived(
    snapshot?.definition.scopes.includes(scope.kind) ?? false,
  );
  const saving = $derived(operation?.status === 'preparing');
  const disabled = $derived(
    reply === null ||
      reply.read_only ||
      saving ||
      snapshot?.pending_operation !== null,
  );

  async function load(signal: AbortSignal): Promise<void> {
    try {
      reply = await readExtensionSettings(route.extensionId, scope, signal);
      loadFailure = null;
      stale = false;
    } catch (error) {
      if (!signal.aborted) loadFailure = messageFrom(error);
    }
  }

  $effect(() => {
    void scope;
    page = new AbortController();
    const signal = page.signal;
    reply = null;
    void load(signal);
    return () => {
      page.abort();
    };
  });

  /** Wait, or reject as soon as the page leaves. */
  function pause(signal: AbortSignal): Promise<void> {
    return new Promise((resolve, reject) => {
      const timer = setTimeout(resolve, OPERATION_POLL_MS);
      signal.addEventListener(
        'abort',
        () => {
          clearTimeout(timer);
          reject(new DOMException('The page closed.', 'AbortError'));
        },
        { once: true },
      );
    });
  }

  async function follow(started: ExtensionOperation): Promise<void> {
    const signal = page.signal;
    let current = started;
    operation = current;
    while (current.status === 'preparing') {
      await pause(signal);
      current = await readExtensionOperation(current.operation_id, signal);
      operation = current;
    }
    await load(signal);
  }

  /** Save form values, or reset the override with null. */
  async function write(value: unknown): Promise<void> {
    if (snapshot === null) return;
    writeFailure = null;
    operation = null;
    try {
      const document =
        value === null ? null : settingsDocument(snapshot, value);
      const started = await saveExtensionSettings(
        route.extensionId,
        scope,
        document,
        snapshot.settings_revision,
        page.signal,
      );
      await follow(started);
    } catch (error) {
      if (page.signal.aborted) return;
      stale =
        error instanceof StaleSettingsError ||
        (error instanceof HttpFailure && error.status === CONFLICT);
      writeFailure = stale
        ? new StaleSettingsError().message
        : messageFrom(error);
    }
  }
</script>

<section class="extension-settings" aria-label="Extension settings">
  <p>
    <a href={formatRoute({ kind: 'settings' })}>Extensions</a> /
    <code>{route.extensionId}</code>
  </p>
  <h2>{scopeLabel(scope)}</h2>

  {#if loadFailure !== null}
    <p class="failure" role="alert">{loadFailure}</p>
  {:else if snapshot === null}
    <p class="waiting">loading settings…</p>
  {:else if !declared}
    <p>This extension has no {scope.kind} settings.</p>
  {:else}
    <p class="note">
      {snapshot.override === null
        ? 'This scope uses the inherited values.'
        : 'This scope has its own values.'}
      Revision {snapshot.settings_revision}.
    </p>
    {#if reply?.read_only}
      <p class="note">Settings are read-only in this dashboard.</p>
    {/if}
    {#if schema === null}
      <p class="failure">The package does not declare its settings schema.</p>
    {:else}
      {#key `${String(snapshot.settings_revision)}:${JSON.stringify(scope)}`}
        <SettingsForm
          {schema}
          value={effectiveValue(snapshot)}
          {disabled}
          onsave={(value) => void write(value)}
        />
      {/key}
    {/if}
    {#if snapshot.override !== null}
      <button type="button" {disabled} onclick={() => void write(null)}
        >Reset to inherited values</button
      >
    {/if}

    {#if saving}
      <p role="status">Saving…</p>
    {:else if operation?.status === 'succeeded'}
      <p role="status">Saved. {SETTINGS_EFFECT}</p>
    {:else if operation !== null}
      <p class="failure" role="alert">
        The change did not apply: {operation.failure?.detail ??
          operation.status}. The previous settings stay active.
      </p>
    {/if}
    {#if writeFailure !== null}
      <p class="failure" role="alert">{writeFailure}</p>
      {#if stale}
        <button type="button" onclick={() => void load(page.signal)}
          >Refresh</button
        >
      {/if}
    {/if}

    {#if catalog?.runtimeRevision}
      {#each panels as panel (`${panel.extension_id}:${panel.view_id}`)}
        <ExtensionViewMount
          view={panel}
          {scope}
          runtimeRevision={catalog.runtimeRevision}
        />
      {/each}
    {/if}
    <SecretControls owner={route.extensionId} />
  {/if}
</section>

<style>
  .extension-settings {
    display: flex;
    flex-direction: column;
    gap: 10px;
    max-width: 720px;
    padding: 12px;
  }

  .note,
  .waiting {
    color: var(--dim);
  }

  .failure {
    color: var(--err, #e06c75);
  }
</style>
