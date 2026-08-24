<script lang="ts">
  import { onDestroy, onMount, tick } from 'svelte';

  import type { CommandOption } from './command-menu';
  import {
    commandToken,
    highlightedCommand,
    matchingCommands,
  } from './command-menu';

  const MENU_BLUR_MS = 150;
  const HIGHLIGHT_METRICS = [
    'fontFamily',
    'fontSize',
    'fontWeight',
    'fontStyle',
    'lineHeight',
    'letterSpacing',
    'wordSpacing',
    'tabSize',
    'textIndent',
    'paddingTop',
    'paddingRight',
    'paddingBottom',
    'paddingLeft',
    'borderTopWidth',
    'borderRightWidth',
    'borderBottomWidth',
    'borderLeftWidth',
    'borderRadius',
  ] as const;

  let {
    value = $bindable(''),
    commands,
    textarea,
    host,
    enterSends = true,
    onneedcommands = () => undefined,
  }: {
    value?: string;
    commands: readonly CommandOption[];
    textarea: HTMLTextAreaElement | undefined;
    host: HTMLElement | undefined;
    enterSends?: boolean;
    onneedcommands?: () => void;
  } = $props();

  let menu = $state<HTMLElement>();
  let mirror = $state<HTMLElement>();
  let selected = $state(0);
  let open = $state(false);
  let blurTimer: ReturnType<typeof setTimeout> | null = null;
  let paintFrame: number | null = null;
  let requestedValue = '';

  const token = $derived(commandToken(value));
  const items = $derived(
    token === null ? [] : matchingCommands(commands, token),
  );
  const highlight = $derived(highlightedCommand(value, commands));

  $effect(() => {
    selected = 0;
    open = token !== null && commands.length > 0;
  });

  $effect(() => {
    if (commands.length > 0 || token === null) {
      requestedValue = '';
      return;
    }
    if (requestedValue === value) return;
    requestedValue = value;
    onneedcommands();
  });

  $effect(() => {
    if (
      value.length === 0 ||
      textarea === undefined ||
      host === undefined ||
      highlight === null
    )
      return;
    scheduleHighlight();
  });

  $effect(() => {
    if (!open || items.length === 0 || menu === undefined) return;
    const row = menu.children.item(selected);
    if (row instanceof HTMLElement) row.scrollIntoView({ block: 'nearest' });
  });

  onMount(() => {
    const resize = (): void => {
      scheduleHighlight();
    };
    window.addEventListener('resize', resize);
    textarea?.addEventListener('scroll', resize);
    textarea?.addEventListener('blur', blur);
    const observer =
      typeof ResizeObserver === 'function'
        ? new ResizeObserver(() => {
            scheduleHighlight();
          })
        : null;
    if (textarea !== undefined) observer?.observe(textarea);
    if (host !== undefined) observer?.observe(host);
    return () => {
      window.removeEventListener('resize', resize);
      textarea?.removeEventListener('scroll', resize);
      textarea?.removeEventListener('blur', blur);
      observer?.disconnect();
    };
  });

  onDestroy(() => {
    if (blurTimer !== null) clearTimeout(blurTimer);
    if (paintFrame !== null) cancelAnimationFrame(paintFrame);
  });

  export function handleKey(event: KeyboardEvent): boolean {
    const selectedItem = items[selected];
    if (!open || selectedItem === undefined) return false;
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      const offset = event.key === 'ArrowDown' ? 1 : items.length - 1;
      selected = (selected + offset) % items.length;
      return true;
    }
    if (event.key === 'Tab') {
      event.preventDefault();
      void complete(selectedItem);
      return true;
    }
    if (event.key === 'Escape') {
      event.stopPropagation();
      open = false;
      return true;
    }
    if (event.key === 'Enter' && !event.shiftKey) {
      if (enterSends && token === selectedItem.command) {
        open = false;
        return false;
      }
      event.preventDefault();
      void complete(selectedItem);
      return true;
    }
    return false;
  }

  function scheduleHighlight(): void {
    if (paintFrame !== null) return;
    paintFrame = requestAnimationFrame(() => {
      paintFrame = null;
      paintHighlight();
    });
  }

  function paintHighlight(): void {
    if (
      mirror === undefined ||
      textarea === undefined ||
      host === undefined ||
      highlight === null ||
      textarea.disabled
    )
      return;
    const textareaBox = textarea.getBoundingClientRect();
    const hostBox = host.getBoundingClientRect();
    mirror.style.left = `${String(textareaBox.left - hostBox.left)}px`;
    mirror.style.top = `${String(textareaBox.top - hostBox.top)}px`;
    mirror.style.width = `${String(textareaBox.width)}px`;
    mirror.style.height = `${String(textareaBox.height)}px`;
    const style = getComputedStyle(textarea);
    for (const property of HIGHLIGHT_METRICS)
      mirror.style.setProperty(camelToCss(property), style[property]);
    mirror.scrollTop = textarea.scrollTop;
  }

  async function complete(command: CommandOption): Promise<void> {
    value = `/${command.command} `;
    open = false;
    await tick();
    textarea?.focus();
    textarea?.setSelectionRange(value.length, value.length);
    textarea?.dispatchEvent(new Event('input', { bubbles: true }));
  }

  function blur(): void {
    if (blurTimer !== null) clearTimeout(blurTimer);
    blurTimer = setTimeout(() => {
      open = false;
      blurTimer = null;
    }, MENU_BLUR_MS);
  }

  function camelToCss(property: string): string {
    return property.replace(/[A-Z]/g, (letter) => `-${letter.toLowerCase()}`);
  }
</script>

{#if highlight !== null}
  <div bind:this={mirror} class="cmhl" aria-hidden="true">
    <span class="cmhlt">/{highlight}</span>{value.slice(highlight.length + 1)}
  </div>
{/if}

{#if open && items.length > 0}
  <div bind:this={menu} class="cmenu" role="listbox" aria-label="commands">
    {#each items as item, index (item.command)}
      <button
        class:sel={index === selected}
        class="cmi"
        type="button"
        role="option"
        aria-selected={index === selected}
        onmousedown={(event) => {
          event.preventDefault();
          void complete(item);
        }}
      >
        <span class="cmname">/{item.command}</span>
        {#if item.description.length > 0}
          <span class="cmdesc">{item.description}</span>
        {/if}
      </button>
    {/each}
  </div>
{/if}

<style>
  button.cmi {
    width: 100%;
    border: 0;
    background: transparent;
    text-align: left;
  }
</style>
