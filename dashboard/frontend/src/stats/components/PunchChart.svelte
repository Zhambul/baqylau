<script lang="ts">
  import { WEEKDAYS } from '../charts';
  import type { ApplicationInsights } from '../model';

  const CELL = 20;
  const LEFT = 34;
  const TOP = 4;
  const RADIUS = CELL / 2 - 2;
  const WIDTH = LEFT + 24 * CELL;
  const HEIGHT = TOP + 7 * CELL + 16;

  let { rows }: { rows: ApplicationInsights['hourlySessions'] } = $props();
  const maximum = $derived(Math.max(0, ...rows.map((row) => row.sessionCount)));
</script>

<section class="stsec">
  <h2>When you work</h2>
  <div class="heatscroll">
    <svg
      class="punch"
      viewBox={`0 0 ${String(WIDTH)} ${String(HEIGHT)}`}
      width={WIDTH}
      height={HEIGHT}
    >
      {#each WEEKDAYS as label, row (label)}
        <text x="0" y={TOP + row * CELL + CELL / 2 + 3} class="hmlabel">
          {label}
        </text>
      {/each}
      {#each Array.from({ length: 12 }, (_, index) => index * 2) as hour (hour)}
        <text
          x={LEFT + hour * CELL + CELL / 2}
          y={HEIGHT - 4}
          class="hmlabel"
          text-anchor="middle">{hour}</text
        >
      {/each}
      {#each rows as point (`${String(point.dayOfWeek)}-${String(point.hour)}`)}
        {#if point.sessionCount > 0 && maximum > 0}
          <circle
            cx={LEFT + point.hour * CELL + CELL / 2}
            cy={TOP + point.dayOfWeek * CELL + CELL / 2}
            r={Math.max(2, RADIUS * Math.sqrt(point.sessionCount / maximum))}
            class="punchdot"
          >
            <title>
              {point.sessionCount} session{point.sessionCount === 1 ? '' : 's'} ·
              {WEEKDAYS[point.dayOfWeek] ?? ''}
              {point.hour}:00
            </title>
          </circle>
        {/if}
      {/each}
    </svg>
  </div>
</section>
