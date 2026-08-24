import { describe, expect, it } from 'vitest';

import { translateSessionSnapshot } from '../api/translators/session-data';
import { wireSnapshot } from '../test/session-fixture';
import { groupSessions } from './grouping';
import type { SessionSnapshot } from './model';

const NOW = 1_800_000_000;

function snapshot(
  id: string,
  directory: string,
  live: boolean,
  startedAt: number,
): SessionSnapshot {
  const translated = translateSessionSnapshot(wireSnapshot(id, directory));
  return {
    ...translated,
    live,
    session: {
      ...translated.session,
      startedAt,
      finishedAt: live ? null : startedAt,
      state: live ? 'running' : 'finished',
    },
  };
}

describe('session grouping', () => {
  it('sorts projects and separates active, parked, and archived sessions', () => {
    const groups = groupSessions(
      [
        snapshot('active', '/work/new', true, NOW - 10),
        snapshot('parked', '/work/new', false, NOW - 60),
        snapshot('archived', '/work/old', false, NOW - 400_000),
      ],
      new Map(),
      NOW,
    );

    expect(groups.map((group) => group.workingDirectory)).toEqual([
      '/work/new',
      '/work/old',
    ]);
    expect(groups[0]?.active.map((item) => item.session.sessionId)).toEqual([
      'active',
    ]);
    expect(groups[0]?.parked.map((item) => item.session.sessionId)).toEqual([
      'parked',
    ]);
    expect(groups[1]?.archived.map((item) => item.session.sessionId)).toEqual([
      'archived',
    ]);
  });

  it('restores a hidden directory when a newer session starts there', () => {
    const hidden = new Map([['/work/project', NOW - 100]]);

    expect(
      groupSessions(
        [snapshot('old', '/work/project', false, NOW - 200)],
        hidden,
        NOW,
      ),
    ).toEqual([]);
    expect(
      groupSessions(
        [snapshot('new', '/work/project', false, NOW - 50)],
        hidden,
        NOW,
      ),
    ).toHaveLength(1);
  });
});
