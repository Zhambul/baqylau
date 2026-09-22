<script lang="ts">
  import type { ExtensionViewContext } from '@baqylau/extension-api';
  import { onMount } from 'svelte';
  import { BUILD_LABEL } from './build-label.js';

  let { viewState }: { viewState: { context: ExtensionViewContext } } =
    $props();
  let selected = $state<'diff' | 'tree' | 'threads'>('diff');
  let pulses = $state(0);

  onMount(() => {
    const pulse = (): void => {
      pulses += 1;
    };
    window.addEventListener('fixture-pulse', pulse, {
      signal: viewState.context.signal,
    });
    return () => {
      window.removeEventListener('fixture-pulse', pulse);
    };
  });
</script>

<section aria-label="External extension">
  <h2>Extension {BUILD_LABEL}</h2>
  <p data-testid="settings">
    Settings revision: {viewState.context.settingsRevision}
  </p>
  <p data-testid="pulses">Pulses: {pulses}</p>
  <nav aria-label="Extension views">
    <button
      onclick={() => {
        selected = 'diff';
      }}>Diff</button
    >
    <button
      onclick={() => {
        selected = 'tree';
      }}>Files</button
    >
    <button
      onclick={() => {
        selected = 'threads';
      }}>Threads</button
    >
  </nav>
  {#if selected === 'diff'}
    <pre aria-label="Diff"><span class="removed">- old line</span><br /><span
        class="added">+ new line</span
      ></pre>
  {:else if selected === 'tree'}
    <ul aria-label="Files">
      <li>
        src
        <ul>
          <li>main.py</li>
          <li>settings.py</li>
        </ul>
      </li>
    </ul>
  {:else}
    <details open>
      <summary>Thread: build check</summary>
      <p>Read: build failed.</p>
      <p>Reply: I will check the error.</p>
    </details>
  {/if}
</section>

<style>
  :global(p) {
    color: rgb(180, 90, 210);
  }
  section {
    padding: 1rem;
  }
  nav {
    display: flex;
    gap: 0.5rem;
  }
  button {
    color: var(--baqylau-accent);
    background: transparent;
    border: 1px solid currentColor;
  }
  .removed {
    color: #e47a7a;
  }
  .added {
    color: #7dc990;
  }
</style>
