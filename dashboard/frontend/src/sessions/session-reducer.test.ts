import { describe, expect, it } from 'vitest';

import { actorId, entryId } from '../app/domain-ids';
import type { Entry } from '../entries/model';
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

  it('includes child assignment boundaries in the parent feed', () => {
    const envelope = (id: string, cursor: number) => ({
      entryId: entryId(id),
      cursor,
      actorId: actorId('actor-child'),
      parentActorId: actorId('actor-lead'),
      turnId: 'child-turn',
      occurredAt: cursor,
      summary: null,
    });
    const childMessage = translateEntry(wireEntry(3, 'actor-child'));
    const entries: readonly Entry[] = [
      {
        ...envelope('assignment-start', 1),
        type: 'assignment_started',
        body: {
          assignmentId: 'assignment-one',
          assignedActorName: 'researcher',
          prompt: { text: 'inspect the frontend', mediaType: 'text/plain' },
        },
      },
      childMessage,
      {
        ...envelope('assignment-finish', 2),
        type: 'assignment_finished',
        body: {
          assignmentId: 'assignment-one',
          state: 'succeeded',
          result: { text: 'inspection complete', mediaType: 'text/plain' },
        },
      },
    ];

    expect(
      entriesForActor(entries, actorId('actor-lead')).map(
        (entry) => entry.entryId,
      ),
    ).toEqual(['assignment-start', 'assignment-finish']);
    expect(
      entriesForActor(entries, actorId('actor-child')).map(
        (entry) => entry.entryId,
      ),
    ).toEqual(['assignment-start', 'entry-3', 'assignment-finish']);
  });
});
