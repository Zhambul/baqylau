<script lang="ts">
  import { onDestroy, onMount } from 'svelte';

  import type { SessionId } from '../app/domain-ids';
  import {
    DictationController,
    type DictationTelemetry,
  } from './dictation-controller.svelte';

  let {
    textarea,
    harness,
    workingDirectory,
    sessionId,
    disabled = false,
    onfailure,
    ontelemetry = () => undefined,
  }: {
    textarea: HTMLTextAreaElement | undefined;
    harness: string;
    workingDirectory: string;
    sessionId: SessionId | null;
    disabled?: boolean;
    onfailure: (message: string) => void;
    ontelemetry?: DictationTelemetry;
  } = $props();

  const controller = new DictationController(
    (message) => {
      onfailure(message);
    },
    (name, details) => {
      ontelemetry(name, details);
    },
  );

  const active = $derived(
    controller.state === 'starting' ||
      controller.state === 'capturing' ||
      controller.state === 'streaming' ||
      controller.state === 'stopping',
  );

  onMount(() => {
    const keydown = (event: KeyboardEvent): void => {
      if (event.key !== 'Escape' || !active) return;
      event.stopPropagation();
      controller.stop();
    };
    textarea?.addEventListener('keydown', keydown);
    return () => {
      textarea?.removeEventListener('keydown', keydown);
    };
  });

  onDestroy(() => {
    controller.stop();
  });

  export function stop(): void {
    controller.stop();
  }

  function toggle(): void {
    if (active) {
      controller.stop();
      return;
    }
    if (disabled || textarea === undefined || harness.length === 0) return;
    void controller.start({
      textarea,
      harness,
      workingDirectory,
      sessionId,
    });
  }
</script>

<button
  class:wait={controller.state === 'starting'}
  class:rec={controller.state === 'capturing' ||
    controller.state === 'streaming'}
  class:pre={controller.state === 'capturing'}
  class="micbtn"
  type="button"
  title={active ? 'stop dictation' : 'dictate'}
  disabled={disabled && !active}
  onclick={toggle}
>
  <svg
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="2"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
  >
    <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"></path>
    <path d="M19 10v2a7 7 0 0 1-14 0v-2"></path>
    <path d="M12 19v4"></path>
  </svg>
</button>
