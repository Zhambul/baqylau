<script lang="ts">
  import { onDestroy, tick } from 'svelte';

  import type { HarnessCatalog } from '../harnesses/model';
  import PresentationBody from './PresentationBody.svelte';
  import type { VisibleEntryPresentation } from './presentation';

  type Properties = {
    readonly presentation: VisibleEntryPresentation;
    readonly extraClass: string;
    readonly defaultOpen: boolean;
    readonly rewindModes: HarnessCatalog['rewindModes'];
    readonly rewindOpen: boolean;
    readonly onOpenRewind: (() => void) | undefined;
    readonly onCancelRewind: (() => void) | undefined;
    readonly onRewind: ((mode: string) => void) | undefined;
  };

  let {
    presentation,
    extraClass,
    defaultOpen,
    rewindModes,
    rewindOpen,
    onOpenRewind,
    onCancelRewind,
    onRewind,
  }: Properties = $props();

  let open = $state(false);
  let userSet = $state(false);
  let now = $state(Date.now() / 1_000);
  let timer: ReturnType<typeof setInterval> | null = null;

  const fileDetails = $derived.by(() => {
    if (presentation.kind !== 'file') return null;
    const details = {
      read: { label: 'Read', color: 'rgb(97,175,239)' },
      created: { label: 'Write', color: 'rgb(152,195,121)' },
      updated: { label: 'Edit', color: 'rgb(229,192,123)' },
      deleted: { label: 'Delete', color: 'rgb(224,108,117)' },
      renamed: { label: 'Move', color: 'rgb(229,192,123)' },
    };
    const detail = details[presentation.action];
    return {
      label: detail.label,
      color:
        presentation.state === 'failed' ? 'rgb(224,108,117)' : detail.color,
    };
  });

  $effect(() => {
    if (!userSet) open = defaultOpen;
  });

  $effect(() => {
    if (
      presentation.kind === 'block' &&
      presentation.quiet &&
      presentation.state === null
    ) {
      timer ??= setInterval(() => {
        now = Date.now() / 1_000;
      }, 1_000);
    } else if (timer !== null) {
      clearInterval(timer);
      timer = null;
    }
  });

  onDestroy(() => {
    if (timer !== null) clearInterval(timer);
  });

  function duration(seconds: number): string {
    const total = Math.max(0, Math.round(seconds));
    if (total < 60) return `${String(total)}s`;
    if (total < 3_600)
      return `${String(Math.floor(total / 60))}m ${String(total % 60)}s`;
    return `${String(Math.floor(total / 3_600))}h ${String(Math.floor((total % 3_600) / 60))}m`;
  }

  function blockTail(): string {
    if (presentation.kind !== 'block' || !presentation.quiet) return '';
    if (presentation.state === null)
      return `· ${duration(now - presentation.occurredAt)}`;
    const verdict =
      presentation.state === 'succeeded' ? 'finished' : presentation.state;
    const elapsed =
      presentation.finishedAt === null
        ? ''
        : ` · ${duration(presentation.finishedAt - presentation.occurredAt)}`;
    return `${verdict}${elapsed}`;
  }

  async function copyBody(event: MouseEvent): Promise<void> {
    event.stopPropagation();
    if (!open) {
      userSet = true;
      open = true;
      await tick();
    }
    const target = event.currentTarget;
    if (!(target instanceof Element)) return;
    const block = target.closest('.blk');
    const body = block?.querySelector('.bbody');
    if (!(body instanceof Element)) return;
    await navigator.clipboard.writeText(body.textContent);
  }

  function toggleBlock(event: MouseEvent | KeyboardEvent): void {
    const target = event.target;
    if (target instanceof Element && target.closest('a, button') !== null)
      return;
    if (
      (presentation.kind !== 'block' && presentation.kind !== 'file') ||
      presentation.body.kind === 'empty'
    )
      return;
    userSet = true;
    open = !open;
  }
</script>

{#if presentation.kind === 'message'}
  <div
    class={['msg', presentation.className, extraClass]}
    data-item-group={presentation.group}
  >
    <span class="who"
      >{presentation.label}{#if presentation.conversationKind === 'prompt'}<span
          class="sbadge">✓ sent</span
        >{/if}</span
    >
    {#if presentation.questions.length > 0}
      <div class="md">
        {#each presentation.questions as question (question.questionId)}
          <p>{question.question}</p>
          {#if question.choices.length > 0}
            <ul>
              {#each question.choices as choice (choice.label)}
                <li>{choice.label}</li>
              {/each}
            </ul>
          {/if}
        {/each}
      </div>
    {:else}
      <PresentationBody body={presentation.body} />
    {/if}
    {#if presentation.conversationKind === 'prompt' && onOpenRewind !== undefined}
      <button
        class="rw"
        type="button"
        title="rewind to here"
        onclick={onOpenRewind}>↶</button
      >
      {#if rewindOpen}
        <div class="rwmenu">
          <div class="rwhead">rewind to this message</div>
          {#each rewindModes as mode (mode.value)}
            <button
              class="rwopt"
              type="button"
              onclick={() => onRewind?.(mode.value)}>{mode.displayName}</button
            >
          {/each}
          <button class="rwopt rwx" type="button" onclick={onCancelRewind}
            >cancel</button
          >
        </div>
      {/if}
    {/if}
  </div>
{:else if presentation.kind === 'file'}
  {@const details = fileDetails}
  {#if presentation.body.kind === 'empty'}
    <pre class={['opl', extraClass]} data-item-group={presentation.group}><span
        style:color={details?.color}>{details?.label}</span
      ><span style:color="rgb(92,99,112)">(</span><span
        style:color="rgb(171,178,191)">{presentation.path}</span
      ><span style:color="rgb(92,99,112)">)</span
      >{#if presentation.linesAdded > 0}
        <span style:color="rgb(152,195,121)">+{presentation.linesAdded}</span
        >{/if}{#if presentation.linesRemoved > 0}
        <span style:color="rgb(224,108,117)">−{presentation.linesRemoved}</span
        >{/if}</pre>
  {:else}
    <div
      class={['blk', extraClass]}
      data-item-group={presentation.group}
      data-open={open ? '1' : '0'}
      data-out={presentation.state === 'succeeded' ? 'ok' : 'bad'}
    >
      <div
        class="bhead"
        role="button"
        tabindex="0"
        onclick={toggleBlock}
        onkeydown={(event) => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            toggleBlock(event);
          }
        }}
      >
        <span class="bchips"
          ><span style:color={details?.color}>{details?.label}</span><span
            style:color="rgb(92,99,112)">(</span
          ><span style:color="rgb(171,178,191)">{presentation.path}</span><span
            style:color="rgb(92,99,112)">)</span
          ></span
        >
        <span class="bsum"></span>
        <span class="btail"></span>
        <span class="blinks"
          ><button class="cc" type="button" onclick={copyBody}>⧉copy</button
          ></span
        >
      </div>
      {#if open}
        <div class="bbody"><PresentationBody body={presentation.body} /></div>
      {/if}
    </div>
  {/if}
{:else if presentation.kind === 'block'}
  <div
    class={['blk', extraClass]}
    data-item-group={presentation.group}
    data-open={open ? '1' : '0'}
    data-note={presentation.note ? '1' : undefined}
    data-quiet={presentation.quiet ? '1' : undefined}
    data-out={presentation.state === null
      ? 'run'
      : presentation.state === 'succeeded'
        ? 'ok'
        : 'bad'}
  >
    <div
      class="bhead"
      role="button"
      tabindex="0"
      onclick={toggleBlock}
      onkeydown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          toggleBlock(event);
        }
      }}
    >
      <span class="bchips">
        {#if presentation.header.kind === 'note'}
          <div class="anote">
            <span class="anmark">⏺</span>
            <span class="atext">{presentation.header.label}</span>
          </div>
        {:else if presentation.header.kind === 'shell'}
          <span class="ol"
            ><span class="anmark">⏺</span
            >{#if presentation.header.shellKind !== 'cmd'}<span
                class="operation-label">{presentation.header.label}</span
              >{/if}</span
          >
        {:else}
          <span class="anmark" aria-label={presentation.state ?? 'running'}
            >⏺</span
          >
          <span
            class={['chip', 'operation-label', presentation.header.chipKind]}
            >{presentation.header.label}</span
          >
        {/if}
      </span>
      <span class="bsum">{presentation.summary}</span>
      <span class="btail">
        {#if presentation.quiet}
          <span class={presentation.state === null ? 'chip blive' : 'cqt'}
            >{blockTail()}</span
          >
        {/if}
      </span>
      <span class="blinks">
        {#if presentation.body.kind !== 'empty'}
          <button class="cc" type="button" onclick={copyBody}>⧉copy</button>
        {/if}
      </span>
    </div>
    {#if open}
      <div class="bbody"><PresentationBody body={presentation.body} /></div>
    {/if}
  </div>
{/if}

<style>
  button.cc {
    border: 0;
    background: transparent;
  }
</style>
