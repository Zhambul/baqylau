<script lang="ts">
  import { onDestroy } from 'svelte';

  import type { AppState } from '../app/app-state.svelte';

  let { appState }: { appState: AppState } = $props();

  let now = $state(Date.now());
  let timer: ReturnType<typeof setInterval> | null = null;

  $effect(() => {
    if (appState.pendingLaunch !== null) {
      timer ??= setInterval(() => {
        now = Date.now();
      }, 500);
    } else if (timer !== null) {
      clearInterval(timer);
      timer = null;
    }
  });

  onDestroy(() => {
    if (timer !== null) clearInterval(timer);
  });
</script>

{#if appState.pendingLaunch !== null}
  {@const launch = appState.pendingLaunch}
  <div class="pendcard">
    <div class="pendspin"></div>
    <div class="pendtitle">
      {launch.display.mode === 'resume'
        ? 'resuming session'
        : 'starting session'}
    </div>
    <div class="penddir">{launch.input.workingDirectory}</div>
    <div class="pendchips">
      {#each [launch.display.account, launch.display.model, launch.display.effort].filter((value) => value.length > 0) as chip (chip)}
        <span class="pendchip">{chip}</span>
      {/each}
    </div>
    {#if launch.display.prompt.length > 0}
      <div class="pendprompt">{launch.display.prompt}</div>
    {/if}
    <div class="pendhint">
      {#if now - launch.startedAt > 8_000}
        still waiting… ({Math.round((now - launch.startedAt) / 1_000)}s) — check
        the terminal tab if this goes on
      {:else}
        {launch.display.toolLabel} is booting in a new terminal tab — usually a couple
        of seconds
      {/if}
    </div>
  </div>
{:else if appState.launchFailure !== null}
  <div class="pendcard fail">
    <div class="pendtitle">✗ the session never appeared</div>
    <div class="pendhint">{appState.launchFailure}</div>
    <button
      class="nsbtn"
      type="button"
      onclick={() => {
        appState.cancelLaunchFailure();
        appState.navigate({ kind: 'list' });
      }}>back to sessions</button
    >
  </div>
{:else}
  <div class="waiting">the launch is no longer active</div>
{/if}
