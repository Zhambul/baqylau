<script lang="ts">
  import { formatRoute } from '../../app/route';
  import { duration, timeAgo } from '../../shared/format';
  import {
    actorCardIsHusk,
    actorDisplayName,
    actorEventCount,
    actorGlyph,
    actorState,
  } from '../agent-presentation';
  import type { Actor } from '../model';
  import ContextBar from './ContextBar.svelte';

  let { actor }: { actor: Actor } = $props();

  const state = $derived(actorState(actor));
  const eventCount = $derived(actorEventCount(actor));
  const age = $derived(
    actor.startedAt === null
      ? ''
      : actor.finishedAt === null
        ? timeAgo(actor.startedAt)
        : duration(actor.finishedAt - actor.startedAt),
  );
</script>

<a
  class:husk={actorCardIsHusk(actor)}
  class="acard"
  data-st={state.className}
  href={formatRoute({
    kind: 'session',
    sessionId: actor.sessionId,
    actorId: actor.actorId,
    tab: 'mirror',
  })}
>
  <div class="actorId">{actorGlyph(actor)} {actorDisplayName(actor)}</div>
  {#if actor.description !== null}
    <div class="desc">{actor.actorId}</div>
  {/if}
  <div class="meta">
    <span class={state.className}>{state.label}</span>
    {#if actor.model !== null}
      <span class="amodel"
        >{actor.model}{actor.effort === null ? '' : `·${actor.effort}`}</span
      >
    {/if}
    <span>{eventCount} events</span>
    {#if age.length > 0}<span>{age}</span>{/if}
  </div>
  {#if actor.context.windowTokens > 0}
    <ContextBar
      used={actor.context.usedTokens}
      window={actor.context.windowTokens}
      compacting={false}
    />
  {/if}
</a>
