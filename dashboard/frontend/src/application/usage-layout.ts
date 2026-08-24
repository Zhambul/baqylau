import type { UsageRow, UsageWindow } from './model';

export type UsageColumn = {
  readonly slot: string;
  readonly index: number;
  readonly label: string;
  readonly scope: UsageWindow['scope'];
  readonly hosts: ReadonlySet<string>;
};

export type UsageTracks = {
  readonly name: number;
  readonly badge: number;
  readonly firstBar: number;
  readonly tail: number;
  readonly count: number;
};

export function accountName(row: UsageRow): string {
  return row.accountId === null
    ? row.displayName
    : `${row.accountId} · ${row.displayName}`;
}

function usageSlot(window: UsageWindow): string {
  return window.durationMinutes !== null && window.durationMinutes > 0
    ? `m${String(window.durationMinutes)}`
    : `k${window.key}`;
}

export function windowsBySlot(
  row: UsageRow,
): ReadonlyMap<string, readonly UsageWindow[]> {
  const slots = new Map<string, UsageWindow[]>();
  for (const window of row.windows) {
    const key = usageSlot(window);
    const windows = slots.get(key) ?? [];
    windows.push(window);
    slots.set(key, windows);
  }
  return slots;
}

export function usageColumns(
  rows: readonly UsageRow[],
): readonly UsageColumn[] {
  const slotOrder: string[] = [];
  const columnsBySlot = new Map<
    string,
    { label: string; scope: UsageWindow['scope']; hosts: Set<string> }[]
  >();
  for (const row of rows) {
    for (const [slot, windows] of windowsBySlot(row)) {
      let seeds = columnsBySlot.get(slot);
      if (seeds === undefined) {
        seeds = [];
        columnsBySlot.set(slot, seeds);
        slotOrder.push(slot);
      }
      windows.forEach((window, index) => {
        let seed = seeds[index];
        if (seed === undefined) {
          seed = {
            label: window.label,
            scope: window.scope,
            hosts: new Set(),
          };
          seeds[index] = seed;
        }
        seed.hosts.add(row.harness);
      });
    }
  }
  slotOrder.sort((left, right) => slotMinutes(left) - slotMinutes(right));
  return slotOrder.flatMap((slot) =>
    (columnsBySlot.get(slot) ?? []).map((seed, index) => ({
      slot,
      index,
      label: seed.label,
      scope: seed.scope,
      hosts: seed.hosts,
    })),
  );
}

export function usageTracks(
  columnCount: number,
  hasAuthenticationError: boolean,
): UsageTracks {
  const firstBar = 2 + (hasAuthenticationError ? 1 : 0);
  return {
    name: 1,
    badge: hasAuthenticationError ? 2 : 0,
    firstBar,
    tail: firstBar + columnCount,
    count: firstBar + columnCount,
  };
}

function slotMinutes(slot: string): number {
  if (!slot.startsWith('m')) return Number.MAX_SAFE_INTEGER;
  const minutes = Number.parseInt(slot.slice(1), 10);
  return Number.isFinite(minutes) ? minutes : Number.MAX_SAFE_INTEGER;
}
