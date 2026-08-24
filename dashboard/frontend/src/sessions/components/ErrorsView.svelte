<script lang="ts">
  import type { SessionApplication } from '../../application/session-model';

  let { errors }: { errors: SessionApplication['errors'] } = $props();
</script>

<div class="errs">
  {#if errors.length === 0}
    <div class="empty">no swallowed exceptions — clean session</div>
  {:else}
    {#each errors as error (error.errorId)}
      <div class="err">
        <div class="h">
          ⚠ {error.component || '?'} · {error.action || '?'}
          {#if error.timestamp > 0}
            · {new Date(error.timestamp * 1000).toLocaleString()}
          {/if}
        </div>
        {#if error.traceback}<pre>{error.traceback}</pre>{/if}
      </div>
    {/each}
  {/if}
</div>
