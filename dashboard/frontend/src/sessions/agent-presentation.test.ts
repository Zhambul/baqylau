import { describe, expect, it } from 'vitest';

import { actorId, sessionId } from '../app/domain-ids';
import {
  actorDisplayName,
  actorEventCount,
  actorGlyph,
  actorState,
  sortedChildActors,
} from './agent-presentation';
import type { Actor } from './model';

function sample(overrides: Partial<Actor> = {}): Actor {
  return {
    sessionId: sessionId('session-one'),
    actorId: actorId('actor-one'),
    parentActorId: actorId('lead'),
    role: 'child',
    name: 'researcher',
    description: 'Find the source',
    state: 'running',
    startedAt: 10,
    finishedAt: null,
    model: 'opus',
    effort: 'high',
    status: 'working',
    usage: {
      tokens: {
        inputTokens: 0,
        outputTokens: 0,
        cacheReadTokens: 0,
        cacheWriteTokens: 0,
        oneHourCacheWriteTokens: 0,
      },
      costInUsd: null,
    },
    context: { usedTokens: 0, windowTokens: 0, compacting: false },
    background: {
      runningShellIds: [],
      monitorCount: 0,
      backgroundJobCount: 0,
    },
    statistics: {
      promptCount: 0,
      shellCommandCount: 0,
      failedShellCommandCount: 0,
      fileCount: 0,
      linesAdded: 0,
      linesRemoved: 0,
      actorMessageCount: 0,
      toolCounts: [
        { tool: 'Read', count: 2 },
        { tool: 'Edit', count: 3 },
      ],
      activeSeconds: 0,
      active: true,
    },
    ...overrides,
  };
}

describe('agent presentation', () => {
  it('derives the agent card from canonical actor facts', () => {
    const actor = sample({ role: 'teammate' });

    expect(actorGlyph(actor)).toBe('◈');
    expect(actorDisplayName(actor)).toBe('Find the source');
    expect(actorEventCount(actor)).toBe(5);
    expect(actorState(actor)).toEqual({
      label: 'running',
      className: 'st-run',
    });
  });

  it('sorts placeholder actors after described actors', () => {
    const olderHusk = sample({
      actorId: actorId('husk'),
      name: 'husk',
      description: null,
      startedAt: 1,
    });
    const newerAgent = sample({
      actorId: actorId('agent'),
      name: 'agent',
      description: 'Real work',
      startedAt: 20,
    });

    expect(sortedChildActors([olderHusk, newerAgent])).toEqual([
      newerAgent,
      olderHusk,
    ]);
  });
});
