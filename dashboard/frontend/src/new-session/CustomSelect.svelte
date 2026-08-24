<script lang="ts">
  import { onDestroy } from 'svelte';

  export type SelectOption = {
    readonly value: string;
    readonly label: string;
    readonly disabled?: boolean;
  };

  let {
    value = $bindable(''),
    options,
    label,
    disabled = false,
    onchange,
  }: {
    value?: string;
    options: readonly SelectOption[];
    label: string;
    disabled?: boolean;
    onchange?: (value: string) => void;
  } = $props();

  let open = $state(false);
  let activeIndex = $state(0);
  let blurTimer: ReturnType<typeof setTimeout> | null = null;
  const selected = $derived(
    options.find((option) => option.value === value) ?? options[0] ?? null,
  );

  onDestroy(() => {
    if (blurTimer !== null) clearTimeout(blurTimer);
  });

  function choose(option: SelectOption): void {
    if (option.disabled === true) return;
    value = option.value;
    open = false;
    onchange?.(option.value);
  }

  function move(delta: number): void {
    if (options.length === 0) return;
    let next = activeIndex;
    let tries = 0;
    while (tries < options.length) {
      next = (next + delta + options.length) % options.length;
      if (options[next]?.disabled !== true) {
        activeIndex = next;
        return;
      }
      tries += 1;
    }
  }

  function keydown(event: KeyboardEvent): void {
    if (event.key === 'Escape') {
      open = false;
      return;
    }
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      if (!open) open = true;
      move(event.key === 'ArrowDown' ? 1 : -1);
      return;
    }
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      if (!open) {
        open = true;
        activeIndex = Math.max(
          0,
          options.findIndex((option) => option.value === value),
        );
        return;
      }
      const option = options[activeIndex];
      if (option !== undefined) choose(option);
    }
  }

  function blur(): void {
    blurTimer = setTimeout(() => {
      blurTimer = null;
      open = false;
    }, 150);
  }
</script>

<div class="nsdrop">
  <button
    class="nsinput nsdropbtn"
    type="button"
    aria-label={label}
    aria-haspopup="listbox"
    aria-expanded={open}
    {disabled}
    onblur={blur}
    onkeydown={keydown}
    onclick={() => {
      open = !open;
    }}
  >
    <span class="nsdroplab">{selected?.label ?? '—'}</span>
    <span class="nsdropcaret">▾</span>
  </button>
  {#if open}
    <div class="nsdropmenu" role="listbox" aria-label={label}>
      {#each options as option, index (option.value)}
        <button
          class:sel={option.value === value || index === activeIndex}
          class="nsdropitem"
          type="button"
          role="option"
          aria-selected={option.value === value}
          disabled={option.disabled === true}
          onmousedown={(event) => {
            event.preventDefault();
          }}
          onclick={() => {
            choose(option);
          }}>{option.label}</button
        >
      {/each}
    </div>
  {/if}
</div>

<style>
  button.nsdropitem {
    display: block;
    width: 100%;
    border: 0;
    background: transparent;
    text-align: left;
  }

  button.nsdropitem:disabled {
    opacity: 0.45;
  }
</style>
