<script lang="ts">
  import { onMount } from 'svelte';

  import type { ExtensionAction } from '../api/extensions';

  import { extensionRows } from './catalog';
  import ExtensionCard from './ExtensionCard.svelte';
  import { ExtensionManagement } from './management.svelte';
  import ShutdownEvidence from './ShutdownEvidence.svelte';

  const view = new ExtensionManagement();
  let confirmationDialog = $state<HTMLDialogElement | undefined>();
  let returnFocus: HTMLElement | null = null;
  const lastShutdown = $derived(view.runtime?.last_shutdown ?? null);
  const rows = $derived(
    view.catalog === null || view.runtime === null
      ? []
      : extensionRows(view.catalog, view.runtime),
  );

  onMount(() => {
    void view.refresh();
    return () => {
      view.close();
    };
  });

  $effect(() => {
    if (confirmationDialog === undefined) return;
    if (view.confirmation !== null && !view.busy && !confirmationDialog.open)
      confirmationDialog.showModal();
    if (view.confirmation === null && confirmationDialog.open) {
      confirmationDialog.close();
      if (returnFocus?.isConnected === true) returnFocus.focus();
    }
  });
</script>

<section class="extension-settings" aria-labelledby="settings-title">
  <header class="settings-heading">
    <div>
      <h1 id="settings-title">Settings</h1>
      <h2>Extensions</h2>
    </div>
    <div class="actions">
      <button
        class="ghost"
        type="button"
        disabled={view.busy}
        onclick={() => {
          void view.refresh();
        }}>Refresh</button
      >
      <button
        class="ghost"
        type="button"
        disabled={!view.canWrite || view.confirmation !== null}
        onclick={() => {
          void view.rescan();
        }}>Rescan packages</button
      >
    </div>
  </header>
  <p>
    Manage trusted local packages. Changes apply to future processing; stored
    history is retained.
  </p>
  {#if view.error !== null}<p role="alert" class="failure">{view.error}</p>{/if}
  {#if view.catalog === null || view.runtime === null}
    <p role="status">
      {view.busy
        ? 'Loading extensions…'
        : 'Extension management is unavailable. Use Refresh to try again.'}
    </p>
  {:else}
    <div class="runtime" role="status">
      <span>Runtime: {view.runtime.phase}</span>
      {#if view.busy}<span>Checking state…</span>{/if}
      {#if !view.fresh}<span
          >Displayed state needs refresh. Changes are disabled.</span
        >{/if}
    </div>
    {#if view.runtime.read_only}
      <p class="notice">
        Extension changes are read-only. This does not make other application
        controls read-only.
      </p>
    {/if}
    {#if view.operation !== null}
      <section
        class="notice"
        aria-label="Last observed operation"
        aria-live="polite"
      >
        <h3>Last observed operation</h3>
        <p>
          {view.operation.kind}: {view.operation.extension_id ?? 'runtime'} — {view
            .operation.status}
        </p>
        <p class="path">Operation: {view.operation.operation_id}</p>
        {#if view.operation.status === 'preparing'}
          <p>The request is accepted. The new runtime is not yet active.</p>
        {/if}
        {#if view.operation.failure !== null}
          <p class="failure">
            {view.operation.failure.code}: {view.operation.failure.detail}
          </p>
        {/if}
      </section>
    {/if}
    {#if view.runtime.cleanup_pending || view.runtime.cleanup.length > 0}
      <section class="notice" aria-label="Runtime cleanup">
        <h3>Runtime cleanup</h3>
        {#if view.runtime.cleanup_pending}<p>Cleanup is pending.</p>{/if}
        {#each view.runtime.cleanup as issue, index (`${issue.runtime_revision}:${issue.extension_id ?? ''}:${String(index)}`)}
          <p class="failure">
            {issue.extension_id ?? 'Runtime'}: {issue.reason}
          </p>
          {#if issue.pending_job_ids.length > 0}<p class="path">
              Unresolved jobs: {issue.pending_job_ids.join(', ')}
            </p>{/if}
        {/each}
      </section>
    {/if}
    {#each view.catalog.root_issues as issue (issue.root_path)}
      <p class="failure">{issue.root_path}: {issue.issue.detail}</p>
    {/each}
    {#if lastShutdown !== null}
      <ShutdownEvidence record={lastShutdown} />
    {/if}
    {#if rows.length === 0}
      <p>No extension packages were found in the configured roots.</p>
    {:else}
      <div class="packages">
        {#each rows as row (row.key)}
          <ExtensionCard
            {row}
            blocked={!view.canWrite || view.confirmation !== null}
            onpreview={(
              owner: string,
              action: ExtensionAction,
              digest: string | null,
            ) => {
              returnFocus =
                document.activeElement instanceof HTMLElement
                  ? document.activeElement
                  : null;
              void view.preview(owner, action, digest);
            }}
          />
        {/each}
      </div>
    {/if}
    <p class="footnote">
      Package settings forms and custom panels are not available yet. Complete
      worker health and operation history are also not available on this page.
    </p>
  {/if}
</section>

<dialog
  bind:this={confirmationDialog}
  aria-labelledby="extension-confirm-title"
  oncancel={(event) => {
    if (view.busy) event.preventDefault();
    else view.cancel();
  }}
>
  {#if view.confirmation !== null}
    <h2 id="extension-confirm-title">Review extension change</h2>
    <p>
      {view.confirmation.plan.request.action}:
      <strong>{view.confirmation.plan.extension_id}</strong>
    </p>
    <p>This change affects:</p>
    <ul>
      {#each view.confirmation.plan.affected_extensions as owner (owner)}<li>
          {owner}
        </li>{/each}
    </ul>
    {#if view.confirmation.plan.request.package_digest !== null}
      <p class="path">
        Selected digest: {view.confirmation.plan.request.package_digest}
      </p>
    {/if}
    {#if view.error !== null}<p role="alert" class="failure">{view.error}</p>
      <p>
        A retry keeps the same request ID. It does not submit a second
        operation.
      </p>{/if}
    <div class="actions">
      <button
        class="ghost"
        type="button"
        disabled={view.busy}
        onclick={() => {
          view.cancel();
        }}>Cancel</button
      >
      <button
        class="ghost"
        type="button"
        disabled={!view.canWrite}
        onclick={() => {
          void view.confirm();
        }}>Apply change</button
      >
    </div>
  {/if}
</dialog>

<style>
  .extension-settings {
    max-width: 1080px;
    margin: 0 auto;
    padding: 28px var(--gx);
  }
  .settings-heading {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
  }
  h1 {
    margin: 0;
    font-size: 24px;
    font-weight: 600;
  }
  h2 {
    margin: 8px 0;
    font-size: 18px;
    font-weight: 500;
  }
  h3 {
    margin: 0;
    font-size: 14px;
    font-weight: 600;
  }
  .actions,
  .runtime {
    display: flex;
    flex-wrap: wrap;
    gap: 12px;
  }
  .runtime {
    font-size: 13px;
    margin: 20px 0;
  }
  .packages {
    display: grid;
    gap: 14px;
    margin: 20px 0;
  }
  .notice {
    padding: 16px;
    background: var(--panel2);
    border-radius: var(--r);
    margin: 14px 0;
  }
  .failure {
    color: var(--red);
    overflow-wrap: anywhere;
  }
  .path {
    overflow-wrap: anywhere;
    font: 12px var(--mono);
  }
  .footnote {
    color: var(--text-soft);
    font-size: 12px;
  }
  button.ghost {
    color: var(--text);
  }
  button:disabled {
    cursor: not-allowed;
    opacity: 0.5;
  }
  dialog {
    width: min(560px, calc(100vw - 32px));
    padding: 24px;
    border: 1px solid var(--hair2);
    border-radius: var(--r);
    background: var(--bg);
    color: var(--text);
  }
  dialog::backdrop {
    background: rgb(0 0 0 / 65%);
  }
</style>
