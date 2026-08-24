<script lang="ts">
  import { onMount } from 'svelte';

  import { readInsights } from '../../api/insights';
  import { timeAgo } from '../../shared/format';
  import type { ApplicationInsights } from '../model';
  import ContributionHeatmap from './ContributionHeatmap.svelte';
  import ProjectCard from './ProjectCard.svelte';
  import PulseSection from './PulseSection.svelte';
  import PunchChart from './PunchChart.svelte';

  type StatsLoadState = 'loading' | 'ready' | 'failed';

  let loadState = $state<StatsLoadState>('loading');
  let insights = $state<ApplicationInsights | null>(null);

  onMount(() => {
    const controller = new AbortController();
    void readInsights(controller.signal)
      .then((value) => {
        insights = value;
        loadState = 'ready';
      })
      .catch(() => {
        loadState = 'failed';
      });
    return () => {
      controller.abort();
    };
  });
</script>

<div class="stats">
  {#if loadState === 'loading'}
    <div class="empty">loading stats…</div>
  {:else if loadState === 'failed' || insights === null}
    <div class="empty">stats could not be loaded</div>
  {:else if insights.totalSessionCount === 0}
    <div class="empty">no sessions recorded yet</div>
  {:else}
    <div class="statstop">
      <h1 class="statsh1">Insights</h1>
      <div class="statssub">
        <span>{insights.totalSessionCount} sessions all-time</span>
        {#if insights.generatedAt > 0}
          <span class="statsgen">updated {timeAgo(insights.generatedAt)}</span>
        {/if}
      </div>
    </div>
    <PulseSection {insights} />
    <ContributionHeatmap rows={insights.dailySessions} />
    <PunchChart rows={insights.hourlySessions} />
    <section class="stsec">
      <h2>Projects</h2>
      <div class="projcards">
        {#each insights.projects as project (project.workingDirectory)}
          <ProjectCard {project} />
        {/each}
      </div>
    </section>
  {/if}
</div>
