<script lang="ts">
  import type { SessionViewState } from '../session-view-state.svelte';
  import AgentCard from './AgentCard.svelte';

  let { view }: { view: SessionViewState } = $props();
</script>

<div class="sgrid">
  {#if view.childActors.length === 0}
    <div class="empty">no agents in this session</div>
  {:else}
    {#each view.childActors as actor (actor.actorId)}
      <AgentCard {actor} />
    {/each}
  {/if}
</div>
