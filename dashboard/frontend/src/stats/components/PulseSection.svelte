<script lang="ts">
  import { compactNumber, dollars } from '../../shared/format';
  import type { ApplicationInsights, InsightWindow } from '../model';

  type WindowKey = 'lastSevenDays' | 'lastThirtyDays' | 'allTime';

  const WINDOWS = [
    { key: 'lastSevenDays', label: '7 days' },
    { key: 'lastThirtyDays', label: '30 days' },
    { key: 'allTime', label: 'all time' },
  ] as const;

  let { insights }: { insights: ApplicationInsights } = $props();
  let selected = $state<WindowKey>('lastSevenDays');
  const window = $derived<InsightWindow>(insights[selected]);
  const maximum = $derived(
    Math.max(0, ...window.projects.map((project) => project.sessionCount)),
  );

  function barWidth(count: number): string {
    return maximum === 0
      ? '0%'
      : `${String(Math.max(4, (count / maximum) * 100))}%`;
  }
</script>

<section class="stsec">
  <div class="sthead">
    <h2>Pulse</h2>
    <div class="pulsebtns">
      {#each WINDOWS as choice (choice.key)}
        <button
          class:on={selected === choice.key}
          class="pbtn"
          type="button"
          onclick={() => {
            selected = choice.key;
          }}>{choice.label}</button
        >
      {/each}
    </div>
  </div>
  <div class="statgrid">
    <div class="sttile">
      <div class="stval">{window.sessionCount}</div>
      <div class="stlbl">sessions</div>
    </div>
    <div class="sttile">
      <div class:pos={window.activeSessionCount > 0} class="stval">
        {window.activeSessionCount}
      </div>
      <div class="stlbl">active</div>
    </div>
    <div class="sttile">
      <div class="stval">{window.finishedSessionCount}</div>
      <div class="stlbl">ended</div>
    </div>
    <div class="sttile">
      <div class="stval gold">{compactNumber(window.tokenCount)}</div>
      <div class="stlbl">tokens</div>
    </div>
    <div class="sttile">
      <div class="stval cost">{dollars(window.costInUsd)}</div>
      <div class="stlbl">cost</div>
    </div>
    {#if window.errorCount > 0}
      <div class="sttile">
        <div class="stval neg">{window.errorCount}</div>
        <div class="stlbl">errors</div>
      </div>
    {/if}
  </div>
  {#if window.projects.length > 0}
    <div class="pbars">
      {#each window.projects as project (project.workingDirectory)}
        <div class="pbrow">
          <span class="pbname">{project.name}</span>
          <span class="pbtrack"
            ><span class="pbfill" style:width={barWidth(project.sessionCount)}
            ></span></span
          >
          <span class="pbval">{project.sessionCount}</span>
        </div>
      {/each}
    </div>
  {/if}
</section>
