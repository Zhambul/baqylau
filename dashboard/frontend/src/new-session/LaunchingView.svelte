<script lang="ts">
  import type { AppState } from '../app/app-state.svelte';

  let { appState }: { appState: AppState } = $props();
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
