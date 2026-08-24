import { describe, expect, it } from 'vitest';

import { actorId, entryId } from '../app/domain-ids';
import type { Entry } from './model';
import { presentEntry } from './presentation';

describe('entry presentation', () => {
  it('shows a child actor first prompt as a parent-agent message', () => {
    const entry: Entry = {
      type: 'message',
      entryId: entryId('child-prompt'),
      cursor: 1,
      actorId: actorId('child'),
      parentActorId: actorId('lead'),
      turnId: 'child-turn',
      occurredAt: 1,
      summary: null,
      body: {
        messageId: 'message-one',
        role: 'user',
        phase: 'prompt',
        content: { text: 'Inspect the frontend', mediaType: 'text/plain' },
        recipientActorId: null,
        replyTo: null,
      },
    };

    const presentation = presentEntry(entry, new Map());

    expect(presentation).toMatchObject({
      kind: 'message',
      className: 'message',
      conversationKind: 'message',
      label: 'parent agent',
    });
  });
});
