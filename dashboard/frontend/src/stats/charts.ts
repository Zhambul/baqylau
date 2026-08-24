import type { DailySessionCount } from './model';

export const MONTHS = [
  'Jan',
  'Feb',
  'Mar',
  'Apr',
  'May',
  'Jun',
  'Jul',
  'Aug',
  'Sep',
  'Oct',
  'Nov',
  'Dec',
] as const;
export const WEEKDAYS = [
  'Sun',
  'Mon',
  'Tue',
  'Wed',
  'Thu',
  'Fri',
  'Sat',
] as const;

type HeatDay = {
  readonly date: string;
  readonly count: number;
  readonly level: number;
};

export type HeatWeek = {
  readonly month: number;
  readonly days: readonly HeatDay[];
};

function localDay(date: Date): string {
  return [
    String(date.getFullYear()).padStart(4, '0'),
    String(date.getMonth() + 1).padStart(2, '0'),
    String(date.getDate()).padStart(2, '0'),
  ].join('-');
}

function thresholds(rows: readonly DailySessionCount[]): readonly number[] {
  const nonzero = rows
    .map((row) => row.sessionCount)
    .filter((count) => count > 0)
    .sort((first, second) => first - second);
  const quantile = (fraction: number): number =>
    nonzero.length === 0
      ? 0
      : (nonzero[
          Math.min(nonzero.length - 1, Math.floor(fraction * nonzero.length))
        ] ?? 0);
  return [quantile(0.25), quantile(0.5), quantile(0.75)];
}

function heatLevel(count: number, levels: readonly number[]): number {
  if (count === 0) return 0;
  if (count <= (levels[0] ?? 0)) return 1;
  if (count <= (levels[1] ?? 0)) return 2;
  if (count <= (levels[2] ?? 0)) return 3;
  return 4;
}

export function heatWeeks(
  rows: readonly DailySessionCount[],
  today = new Date(),
): readonly HeatWeek[] {
  const counts = new Map(rows.map((row) => [row.date, row.sessionCount]));
  const levels = thresholds(rows);
  const end = new Date(today);
  end.setHours(0, 0, 0, 0);
  const cursor = new Date(end);
  cursor.setDate(cursor.getDate() - 7 * 52 - end.getDay());
  const weeks: HeatWeek[] = [];
  while (cursor <= end) {
    const month = cursor.getMonth();
    const days: HeatDay[] = [];
    for (let weekday = 0; weekday < 7 && cursor <= end; weekday += 1) {
      const date = localDay(cursor);
      const count = counts.get(date) ?? 0;
      days.push({ date, count, level: heatLevel(count, levels) });
      cursor.setDate(cursor.getDate() + 1);
    }
    weeks.push({ month, days });
  }
  return weeks;
}

export function sparklinePoints(
  rows: readonly DailySessionCount[],
  today = new Date(),
): string {
  const width = 220;
  const height = 34;
  const padding = 2;
  const days = 90;
  const counts = new Map(rows.map((row) => [row.date, row.sessionCount]));
  const end = new Date(today);
  end.setHours(0, 0, 0, 0);
  const series = Array.from({ length: days }, (_, index) => {
    const date = new Date(end);
    date.setDate(date.getDate() - (days - 1 - index));
    return counts.get(localDay(date)) ?? 0;
  });
  const maximum = Math.max(1, ...series);
  return series
    .map((count, index) => {
      const x = padding + (index / (series.length - 1)) * (width - 2 * padding);
      const y = height - padding - (count / maximum) * (height - 2 * padding);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ');
}
