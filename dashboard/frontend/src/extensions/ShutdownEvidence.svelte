<script lang="ts">
  import type { ExtensionRuntime } from '../api/extensions';

  let { record }: { record: NonNullable<ExtensionRuntime['last_shutdown']> } =
    $props();
</script>

<details class="shutdown">
  <summary>Last recorded shutdown</summary>
  <p class="identity">Record: {record.record_id}</p>
  <p>
    Resource closure does not mean that an external job completed. Earlier
    records remain in storage.
  </p>
  {#each record.runtimes as runtime (runtime.runtime_revision)}
    <section aria-label={runtime.runtime_revision}>
      <p class="identity">Runtime: {runtime.runtime_revision}</p>
      <p>
        Worker resources: {runtime.resources_closed
          ? 'closed'
          : 'closure not confirmed'}
      </p>
      {#each runtime.issues as issue, index (`${issue.extension_id ?? 'runtime'}:${String(index)}`)}
        <p>{issue.extension_id ?? 'Runtime'}: {issue.reason}</p>
        {#if issue.pending_job_ids.length > 0}
          <p class="identity">
            Unresolved jobs: {issue.pending_job_ids.join(', ')}
          </p>
        {/if}
      {/each}
    </section>
  {/each}
</details>

<style>
  .shutdown {
    margin: 1rem 0;
    padding: 0.75rem;
    border: 1px solid var(--hair2);
    border-radius: var(--r);
  }
  summary {
    cursor: pointer;
  }
  .identity {
    overflow-wrap: anywhere;
  }
</style>
