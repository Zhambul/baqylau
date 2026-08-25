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

function compaction(
  id: string,
  type: 'compaction_started' | 'compaction_finished',
): Entry {
  const common = {
    entryId: entryId(id),
    cursor: Number(id),
    actorId: actorId('lead'),
    parentActorId: null,
    turnId: null,
    occurredAt: Number(id),
    summary: null,
  } as const;
  return type === 'compaction_started'
    ? { ...common, type, body: { beforeTokens: 100 } }
    : {
        ...common,
        type,
        body: { beforeTokens: 100, afterTokens: 20 },
      };
}

describe('feed density', () => {
  it('shows question text for each recorded answer', () => {
    const common = {
      actorId: actorId('lead'),
      parentActorId: null,
      turnId: 'turn',
      occurredAt: 1,
      summary: null,
    } as const;
    const entries: readonly Entry[] = [
      {
        ...common,
        type: 'question_answered',
        entryId: entryId('answer'),
        cursor: 2,
        body: {
          attentionId: 'attention',
          answers: [
            { questionId: '0', labels: ['All 120'] },
            { questionId: '1', labels: ['No comment'] },
          ],
          feedback: null,
        },
      },
      {
        ...common,
        type: 'question_asked',
        entryId: entryId('question'),
        cursor: 1,
        body: {
          attentionId: 'attention',
          questions: [
            {
              questionId: '0',
              title: null,
              question: 'Which incidents do I close to Done?',
              multiple: false,
              choices: [],
            },
            {
              questionId: '1',
              title: null,
              question: 'Add a comment on each closed incident?',
              multiple: false,
              choices: [],
            },
          ],
        },
      },
    ];

    const answer = buildFeedItems(entries, new Map(), [])[0];

    expect(answer?.kind).toBe('message');
    if (answer?.kind !== 'message') throw new Error('answer is not a message');
    expect(answer.body).toEqual({
      kind: 'answers',
      answers: [
        {
          questionId: '0',
          question: 'Which incidents do I close to Done?',
          labels: ['All 120'],
        },
        {
          questionId: '1',
          question: 'Add a comment on each closed incident?',
          labels: ['No comment'],
        },
      ],
      feedback: null,
    });
  });

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

  it('shows one finish for a legacy duplicated compaction', () => {
    const items = buildFeedItems(
      [
        compaction('3', 'compaction_finished'),
        compaction('2', 'compaction_finished'),
        compaction('1', 'compaction_started'),
      ],
      new Map(),
      [],
    );

    expect(
      items.map((item) =>
        item.kind === 'block' && item.header.kind === 'note'
          ? item.header.label
          : '',
      ),
    ).toEqual([
      'Context compacted · 100 → 20 tokens',
      'Compacting the context…',
    ]);
  });
});
