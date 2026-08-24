<script lang="ts">
  import { MONTHS, heatWeeks } from '../charts';
  import type { DailySessionCount } from '../model';

  const CELL = 12;
  const GAP = 3;
  const TOP = 16;
  const LEFT = 26;
  const DAY_LABELS = [
    { row: 1, text: 'Mon' },
    { row: 3, text: 'Wed' },
    { row: 5, text: 'Fri' },
  ] as const;

  let { rows }: { rows: readonly DailySessionCount[] } = $props();
  const weeks = $derived(heatWeeks(rows));
  const width = $derived(LEFT + weeks.length * (CELL + GAP));
  const height = TOP + 7 * (CELL + GAP);
</script>

<section class="stsec">
  <h2>Contributions</h2>
  <div class="heatscroll">
    <svg
      class="heat"
      viewBox={`0 0 ${String(width)} ${String(height)}`}
      {width}
      {height}
    >
      {#each weeks as week, weekIndex (week.days[0]?.date ?? weekIndex)}
        {#if weekIndex === 0 || weeks[weekIndex - 1]?.month !== week.month}
          <text x={LEFT + weekIndex * (CELL + GAP)} y="11" class="hmlabel">
            {MONTHS[week.month] ?? ''}
          </text>
        {/if}
        {#each week.days as day, dayIndex (day.date)}
          <rect
            x={LEFT + weekIndex * (CELL + GAP)}
            y={TOP + dayIndex * (CELL + GAP)}
            width={CELL}
            height={CELL}
            rx="2"
            class={`hm l${String(day.level)}`}
          >
            <title
              >{day.count} session{day.count === 1 ? '' : 's'} on {day.date}</title
            >
          </rect>
        {/each}
      {/each}
      {#each DAY_LABELS as label (label.row)}
        <text
          x="0"
          y={TOP + label.row * (CELL + GAP) + CELL - 2}
          class="hmlabel">{label.text}</text
        >
      {/each}
    </svg>
  </div>
  <div class="hmlegend">
    <span class="hmleglbl">less</span>
    {#each [0, 1, 2, 3, 4] as level (level)}
      <svg width={CELL} height={CELL} class="hmswatch">
        <rect width={CELL} height={CELL} rx="2" class={`hm l${String(level)}`}
        ></rect>
      </svg>
    {/each}
    <span class="hmleglbl">more</span>
  </div>
</section>
