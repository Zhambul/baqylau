import { describe, expect, it } from 'vitest';

import type { UsageRow, UsageWindow } from './model';
import { usageColumns, usageTracks } from './usage-layout';

function window(
  key: string,
  minutes: number,
  scope: UsageWindow['scope'] = 'account',
): UsageWindow {
  return {
    key,
    label: key,
    usedPercent: '1',
    resetsAt: null,
    durationMinutes: minutes,
    scope,
    modelId: null,
  };
}

function row(harness: string, windows: readonly UsageWindow[]): UsageRow {
  return {
    harness,
    accountId: null,
    displayName: harness,
    switchable: false,
    defaultForLaunch: false,
    plan: null,
    windows,
    schedulingScore: null,
    schedulingAllowed: true,
    limit: null,
    authenticationError: null,
    collectionError: null,
  };
}

describe('account usage layout', () => {
  it('aligns different host keys by duration and keeps model caps adjacent', () => {
    const columns = usageColumns([
      row('claude', [
        window('five_hour', 300),
        window('seven_day', 10_080),
        window('seven_day_fable', 10_080, 'model'),
      ]),
      row('codex', [window('w10080', 10_080)]),
    ]);

    expect(columns.map((column) => column.slot)).toEqual([
      'm300',
      'm10080',
      'm10080',
    ]);
    expect(columns[1]?.hosts).toEqual(new Set(['claude', 'codex']));
  });

  it('reserves one shared authentication-warning track', () => {
    expect(usageTracks(2, true)).toEqual({
      name: 1,
      badge: 2,
      firstBar: 3,
      tail: 5,
      count: 5,
    });
  });
});
