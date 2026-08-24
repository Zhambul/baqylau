import { describe, expect, it } from 'vitest';

import { wireActor, wireEntry, wireSession } from '../test/session-fixture';
import {
  StreamValidationFailure,
  decodeGlobalStreamFrame,
  decodeReadyFrame,
  decodeSessionStreamFrame,
} from './stream-decoder';

describe('stream decoder', () => {
  it('decodes the ready protocol and a complete global delta', () => {
    expect(decodeReadyFrame('{"boot_id":"boot-one"}')).toBe('boot-one');

    const frame = decodeGlobalStreamFrame(
      JSON.stringify({ sessions: [wireSession()], actors: [wireActor()] }),
    );

    expect(frame.sessions[0]?.sessionId).toBe('session-one');
    expect(frame.actors[0]?.actorId).toBe('actor-lead');
  });

  it('rejects malformed JSON and unknown discriminant values', () => {
    expect(() => decodeReadyFrame('not-json')).toThrow(StreamValidationFailure);
    expect(() =>
      decodeGlobalStreamFrame(
        JSON.stringify({
          sessions: [],
          actors: [{ ...wireActor(), status: 'surprised' }],
        }),
      ),
    ).toThrow(/unknown actor status/);
  });

  it('decodes session deltas and validates entry discriminants at runtime', () => {
    const frame = decodeSessionStreamFrame(
      JSON.stringify({
        session: wireSession(),
        actors: [wireActor()],
        entries: [wireEntry(43)],
      }),
    );

    expect(frame.session?.sessionId).toBe('session-one');
    expect(frame.entries[0]?.entryId).toBe('entry-43');

    expect(() =>
      decodeSessionStreamFrame(
        JSON.stringify({
          session: null,
          actors: [],
          entries: [{ ...wireEntry(44), type: 'unexpected' }],
        }),
      ),
    ).toThrow(/unknown type/);
  });
});
