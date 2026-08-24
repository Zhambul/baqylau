import { describe, expect, it } from 'vitest';

import { actorId } from '../app/domain-ids';
import { wireActor, wireEntry } from '../test/session-fixture';
import { translateActor } from '../api/translators/session-data';
import { translateEntry } from '../api/translators/entries';
import {
  appendOlderEntries,
  entriesForActor,
  initialEntriesNewestFirst,
  mergeActors,
  prependLiveEntries,
} from './session-reducer';

describe('session reducer', () => {
  it('replaces changed actors and adds newly observed actors', () => {
    const lead = translateActor(wireActor());
    const changedLead = translateActor({ ...wireActor(), status: 'idle' });
    const child = translateActor({
      ...wireActor('session-one', 'actor-child'),
      role: 'child',
      parent_actor_id: 'actor-lead',
    });

    const actors = mergeActors([lead], [changedLead, child]);

    expect(actors.map((actor) => actor.actorId)).toEqual([
      'actor-lead',
      'actor-child',
    ]);
    expect(actors[0]?.status).toBe('idle');
  });

  it('keeps a scoped, deduplicated, newest-first feed through live and backfill', () => {
    const initial = [wireEntry(1), wireEntry(2), wireEntry(3)].map(
      translateEntry,
    );
    const newest = initialEntriesNewestFirst(initial);
    const live = [wireEntry(3), wireEntry(4), wireEntry(5, 'actor-child')].map(
      translateEntry,
    );
    const scopedLive = entriesForActor(live, actorId('actor-lead'));
    const updated = prependLiveEntries(newest, scopedLive);
    const older = [wireEntry(-1), wireEntry(0), wireEntry(1)].map(
      translateEntry,
    );
    const complete = appendOlderEntries(updated, older);

    expect(complete.map((entry) => entry.cursor)).toEqual([4, 3, 2, 1, 0, -1]);
  });
});
