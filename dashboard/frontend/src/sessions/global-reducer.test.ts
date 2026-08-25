import { describe, expect, it } from 'vitest';

import {
  translateActor,
  translateSession,
  translateSessionSnapshot,
} from '../api/translators/session-data';
import { wireActor, wireSession, wireSnapshot } from '../test/session-fixture';
import { reduceGlobalDelta } from './global-reducer';

describe('global session reducer', () => {
  it('merges changed facts while it preserves read-time fields', () => {
    const current = translateSessionSnapshot(wireSnapshot());
    const changedSession = translateSession({
      ...wireSession(),
      title: 'Updated title',
    });
    const changedActor = translateActor({
      ...wireActor(),
      status: 'awaiting_response',
    });

    const result = reduceGlobalDelta([current], {
      sessions: [changedSession],
      actors: [changedActor],
    });

    expect(result.sessions[0]?.session.title).toBe('Updated title');
    expect(result.sessions[0]?.actors[0]?.status).toBe('awaiting_response');
    expect(result.sessions[0]?.live).toBe(true);
    expect(result.sessions[0]?.repository?.branch).toBe('main');
  });

  it('adopts a new session once and reports an orphan actor', () => {
    const newSession = translateSession(wireSession('session-new'));
    const orphan = translateActor(wireActor('session-orphan'));

    const result = reduceGlobalDelta([], {
      sessions: [newSession],
      actors: [translateActor(wireActor('session-new')), orphan],
    });

    expect(result.adopt).toEqual(['session-new']);
    expect(result.unknownActorSessions).toEqual(['session-orphan']);
  });

  it('removes a session when a terminal close finishes it', () => {
    const current = translateSessionSnapshot(wireSnapshot());
    const finished = translateSession({
      ...wireSession(),
      state: 'finished',
      finished_at: 1_700_000_100,
    });

    const result = reduceGlobalDelta([current], {
      sessions: [finished],
      actors: [],
    });

    expect(result.sessions).toEqual([]);
    expect(result.adopt).toEqual([]);
  });

  it('adopts a resumed session that is not in the live list', () => {
    const resumed = translateSession(wireSession());

    const result = reduceGlobalDelta([], {
      sessions: [resumed],
      actors: [],
    });

    expect(result.sessions).toEqual([]);
    expect(result.adopt).toEqual(['session-one']);
  });

  it('does not adopt actors from a finished session', () => {
    const result = reduceGlobalDelta([], {
      sessions: [
        translateSession({
          ...wireSession(),
          state: 'finished',
          finished_at: 1_700_000_100,
        }),
      ],
      actors: [translateActor(wireActor())],
    });

    expect(result.sessions).toEqual([]);
    expect(result.adopt).toEqual([]);
    expect(result.unknownActorSessions).toEqual([]);
  });
});
