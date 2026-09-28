<script lang="ts">
  import type {
    ExtensionAction,
    ExtensionHealth,
    ExtensionOperation,
  } from '../api/extensions';
  import { formatRoute } from '../app/route';
  import type { ExtensionRow } from './catalog';

  let {
    row,
    operations,
    health,
    blocked,
    onpreview,
  }: {
    row: ExtensionRow;
    operations: readonly ExtensionOperation[];
    health: ExtensionHealth | null;
    blocked: boolean;
    onpreview: (
      owner: string,
      action: ExtensionAction,
      digest: string | null,
    ) => void;
  } = $props();

  const validSource = $derived(
    row.source?.issue === null && row.source.package_digest !== null,
  );

  function preview(action: ExtensionAction): void {
    if (row.owner !== null)
      onpreview(row.owner, action, row.source?.package_digest ?? null);
  }
</script>

<article aria-label={row.name}>
  <div class="heading">
    <div>
      <h3>{row.name}</h3>
      {#if row.owner !== null}<code>{row.owner}</code>{/if}
    </div>
    <span class:enabled={row.actualState === 'enabled'} class="state"
      >{row.actualState}</span
    >
  </div>
  <dl>
    <div>
      <dt>Requested</dt>
      <dd>{row.requested ? 'enabled' : 'disabled'}</dd>
    </div>
    <div>
      <dt>Active version</dt>
      <dd>{row.activeVersion ?? 'None'}</dd>
    </div>
    <div>
      <dt>Saved version</dt>
      <dd>{row.committedVersion ?? 'None'}</dd>
    </div>
    <div>
      <dt>Source version</dt>
      <dd>{row.source?.package_version ?? 'Unavailable'}</dd>
    </div>
  </dl>
  {#if row.source !== null}
    <p class="path">{row.source.source_path}</p>
    {#if row.source.issue !== null}
      <p class="failure">{row.source.issue.code}: {row.source.issue.detail}</p>
    {/if}
    {#if row.activeDigest !== null && row.source.package_digest !== null && row.activeDigest !== row.source.package_digest}
      <p>
        The source differs from the active package. Reload uses the selected
        source.
      </p>
    {/if}
    <details>
      <summary>Package details</summary>
      <p>
        Capabilities: {row.source.capabilities.join(', ') ||
          'No backend capabilities'}
      </p>
      <p class="path">
        Resolved path: {row.source.resolved_path ?? 'Unavailable'}
      </p>
      <p class="path">
        Source digest: {row.source.package_digest ?? 'Unavailable'}
      </p>
      <p class="path">Active digest: {row.activeDigest ?? 'None'}</p>
    </details>
  {:else}
    <p>
      The source package is not in the current catalog. Saved state is retained.
    </p>
  {/if}
  {#if health !== null && health.state !== 'healthy'}
    <p class="failure" role="status">
      {health.state === 'failed'
        ? 'Disabled after repeated failures'
        : 'Failing'}: {health.consecutive_failures} consecutive failed calls, last
      in {health.last_failure_where ?? 'an unknown stage'}.
    </p>
  {/if}
  {#if operations.length > 0}
    <details>
      <summary>Recent operations</summary>
      <ol class="operations">
        {#each operations as operation (operation.operation_id)}
          <li>
            <span>{operation.kind}</span>
            <span class:failed={operation.status !== 'succeeded'}
              >{operation.status}</span
            >
            <time datetime={new Date(operation.updated_at * 1000).toISOString()}
              >{new Date(operation.updated_at * 1000).toLocaleString()}</time
            >
            {#if operation.failure !== null}
              <p class="failure">
                {operation.failure.code}: {operation.failure.detail}
              </p>
            {/if}
          </li>
        {/each}
      </ol>
    </details>
  {/if}
  {#if row.owner !== null}
    <div class="actions">
      {#if row.activeVersion === null}
        <button
          class="ghost"
          type="button"
          disabled={blocked || !validSource}
          onclick={() => {
            preview('enable');
          }}>Enable</button
        >
      {:else}
        <button
          class="ghost"
          type="button"
          disabled={blocked || !validSource}
          onclick={() => {
            preview('reload');
          }}>Reload</button
        >
      {/if}
      {#if row.activeVersion !== null || row.requested}
        <button
          class="ghost"
          type="button"
          disabled={blocked}
          onclick={() => {
            preview('disable');
          }}>Disable</button
        >
      {/if}
      <a
        href={formatRoute({
          kind: 'extension-settings',
          extensionId: row.owner,
        })}>Settings</a
      >
    </div>
  {/if}
</article>

<style>
  .operations {
    margin: 6px 0 0;
    padding-left: 18px;
  }
  .operations li {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }
  .operations .failed {
    color: var(--err, #e06c75);
  }

  article {
    padding: 20px;
    border-radius: var(--r);
    box-shadow: var(--card);
    background: var(--panel);
  }
  .heading {
    display: flex;
    align-items: start;
    justify-content: space-between;
    gap: 16px;
  }
  h3 {
    margin: 0;
    font-size: 16px;
    font-weight: 600;
  }
  code,
  .path {
    font-size: 12px;
    overflow-wrap: anywhere;
  }
  .state {
    font-size: 12px;
    padding: 2px 8px;
    border: 1px solid var(--hair2);
    border-radius: var(--r);
  }
  .enabled {
    color: var(--green);
  }
  dl {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
    gap: 12px;
    margin: 18px 0;
  }
  dt {
    color: var(--text-soft);
    font-size: 12px;
  }
  dd {
    margin: 2px 0 0;
  }
  p {
    margin: 10px 0;
  }
  .failure {
    color: var(--red);
    overflow-wrap: anywhere;
  }
  summary {
    cursor: pointer;
  }
  .actions {
    display: flex;
    gap: 8px;
    margin-top: 18px;
  }
  button.ghost {
    color: var(--text);
  }
  button:disabled {
    cursor: not-allowed;
    opacity: 0.5;
  }
</style>
