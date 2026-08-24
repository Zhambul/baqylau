import { describe, expect, it } from 'vitest';

import { formatRoute, parseHash, startupNavigation } from './route';

const ROUTES = [
  '#/',
  '#/stats',
  '#/launching',
  '#/s/session-one',
  '#/s/session-one/jobs',
  '#/s/session-one/m/task-one',
  '#/s/session-one/j/task-one',
  '#/s/session-one/a/actor-one',
  '#/s/session-one/a/actor-one/errors',
  '#/s/session-one/a/actor-one/m/task-one',
  '#/s/session-one/a/actor-one/j/task-one',
] as const;

describe('hash routes', () => {
  it.each(ROUTES)('round-trips %s', (hash) => {
    const route = parseHash(hash);
    expect(route.kind).not.toBe('not-found');
    if (route.kind !== 'not-found') {
      expect(formatRoute(route)).toBe(hash);
    }
  });

  it('decodes and encodes domain identifiers', () => {
    const route = parseHash('#/s/a%20session/a/an%20actor/j/a%2Ftask');
    expect(route).toMatchObject({
      actorId: 'an actor',
      detail: { kind: 'job', taskId: 'a/task' },
      kind: 'session',
      sessionId: 'a session',
      tab: 'jobs',
    });
    if (route.kind !== 'not-found') {
      expect(formatRoute(route)).toBe(
        '#/s/a%20session/a/an%20actor/j/a%2Ftask',
      );
    }
  });

  it.each([
    '#/unknown',
    '#/s/',
    '#/s/session/a',
    '#/s/session/m',
    '#/s/session/nope',
  ])('rejects invalid shape %s', (hash) => {
    expect(parseHash(hash)).toEqual({ kind: 'not-found', hash });
  });
});

describe('startup links', () => {
  it('turns a notification query into the canonical session hash', () => {
    expect(startupNavigation('', '?s=session%20one')).toEqual({
      hash: '#/s/session%20one',
      openNewSession: false,
      consumeQuery: true,
    });
  });

  it('does not override an explicit hash', () => {
    expect(startupNavigation('#/stats', '?new=1')).toEqual({
      hash: '#/stats',
      openNewSession: false,
      consumeQuery: false,
    });
  });
});
