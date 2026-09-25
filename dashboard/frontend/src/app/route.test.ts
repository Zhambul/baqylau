import { describe, expect, it } from 'vitest';

import { formatRoute, parseHash, startupNavigation } from './route';

const ROUTES = [
  '#/',
  '#/stats',
  '#/settings/extensions',
  '#/settings/extensions/test.git',
  '#/settings/extensions/test.git/w/workspace-one',
  '#/launching',
  '#/s/session-one',
  '#/s/session-one/jobs',
  '#/s/session-one/m/task-one',
  '#/s/session-one/j/task-one',
  '#/s/session-one/a/actor-one',
  '#/s/session-one/a/actor-one/errors',
  '#/s/session-one/a/actor-one/m/task-one',
  '#/s/session-one/a/actor-one/j/task-one',
  '#/s/session-one/x/test.logs/test.logs.main',
  '#/s/session-one/a/actor-one/x/test.logs/test.logs.main',
  '#/w/workspace-one/x/test.git/test.git.page',
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
    '#/settings',
    '#/settings/unknown',
    '#/settings/extensions/test.git/w',
    '#/settings/extensions/test.git/x/workspace-one',
    '#/settings/extensions/test.git/w/one/two',
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

describe('extension view routes', () => {
  const view = { extensionId: 'test.logs', viewId: 'test.logs.main' };

  it('reads a session extension view', () => {
    expect(parseHash('#/s/session-one/x/test.logs/test.logs.main')).toEqual({
      kind: 'session',
      sessionId: 'session-one',
      tab: 'mirror',
      extensionView: view,
    });
  });

  it('reads a workspace page and rejects incomplete forms', () => {
    expect(parseHash('#/w/workspace-one/x/test.logs/test.logs.main')).toEqual({
      kind: 'extension-page',
      workspaceId: 'workspace-one',
      view,
    });
    expect(parseHash('#/w/workspace-one/x/test.git').kind).toBe('not-found');
    expect(parseHash('#/s/session-one/x/test.logs').kind).toBe('not-found');
  });

  it('keeps a repository directory with slashes and spaces in one segment', () => {
    const route = {
      kind: 'repository-page',
      directory: '/work/my project',
      view,
    } as const;

    const hash = formatRoute(route);

    expect(hash).toBe(
      '#/repo/%2Fwork%2Fmy%20project/x/test.logs/test.logs.main',
    );
    expect(parseHash(hash)).toEqual(route);
    expect(parseHash('#/repo/%2Fwork/x/test.logs').kind).toBe('not-found');
  });
});
