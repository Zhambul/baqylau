import { describe, expect, it } from 'vitest';

import { actorId, entryId } from '../app/domain-ids';
import type { Entry } from './model';
import { buildFeedItems, planDensity } from './feed-model';

function message(
  id: string,
  role: 'user' | 'assistant' | 'system',
  phase: 'prompt' | 'intermediate' | 'end_turn' | 'synthetic',
): Entry {
  return {
    type: 'message',
    entryId: entryId(id),
    cursor: Number(id),
    actorId: actorId('lead'),
    parentActorId: null,
    turnId: 'turn',
    occurredAt: Number(id),
    summary: null,
    body: {
      messageId: `message-${id}`,
      role,
      phase,
      content: { text: id, mediaType: 'text/plain' },
      recipientActorId: null,
      replyTo: null,
    },
  };
}

describe('feed density', () => {
  it('keeps only the final assistant reply in settled focus turns', () => {
    const items = buildFeedItems(
      [
        message('4', 'assistant', 'end_turn'),
        message('3', 'assistant', 'intermediate'),
        message('2', 'user', 'prompt'),
      ],
      new Map(),
      [],
    );
    const units = planDensity(items, 'focus', false, new Set());
    expect(
      units.flatMap((unit) =>
        unit.kind === 'item' && unit.item.kind === 'message'
          ? [
              unit.item.body.kind === 'content'
                ? unit.item.body.content.text
                : '',
            ]
          : [],
      ),
    ).toEqual(['4', '2']);
  });

  it('hides synthetic prompts outside verbose mode', () => {
    const items = buildFeedItems(
      [message('2', 'system', 'synthetic'), message('1', 'user', 'prompt')],
      new Map(),
      [],
    );
    expect(planDensity(items, 'default', false, new Set())).toHaveLength(1);
    expect(planDensity(items, 'verbose', false, new Set())).toHaveLength(2);
  });
});
