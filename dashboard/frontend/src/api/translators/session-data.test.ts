import { describe, expect, it } from 'vitest';

import { leadActor } from '../../sessions/model';
import { wireSnapshot } from '../../test/session-fixture';
import { translateSessionList, translateSessionSnapshot } from './session-data';

describe('session-data translator', () => {
  it('maps every list and actor identity to domain names', () => {
    const snapshot = translateSessionSnapshot(wireSnapshot());
    const lead = leadActor(snapshot);

    expect(snapshot.session.sessionId).toBe('session-one');
    expect(snapshot.session.leadActorId).toBe('actor-lead');
    expect(snapshot.session.tasks[0]?.ownerActorId).toBe('actor-lead');
    expect(lead?.actorId).toBe('actor-lead');
    expect(lead?.statistics.shellCommandCount).toBe(4);
    expect(lead?.usage.tokens.oneHourCacheWriteTokens).toBe(5);
    expect(snapshot.repository).toEqual({
      branch: 'main',
      worktree: null,
      dirty: true,
    });
    expect('id' in (lead ?? {})).toBe(false);
  });

  it('keeps the snapshot cursor that anchors the stream', () => {
    const list = translateSessionList({
      cursor: 51,
      sessions: [wireSnapshot()],
    });

    expect(list.cursor).toBe(51);
    expect(list.sessions).toHaveLength(1);
  });
});
