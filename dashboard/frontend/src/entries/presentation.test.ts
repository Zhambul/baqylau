import { describe, expect, it } from 'vitest';

import { actorId, entryId } from '../app/domain-ids';
import type { Entry } from './model';
import { presentEntry } from './presentation';

describe('entry presentation', () => {
  it('passes a file path to the source highlighter', () => {
    const entry: Entry = {
      type: 'file',
      entryId: entryId('file-one'),
      cursor: 1,
      actorId: actorId('lead'),
      parentActorId: null,
      turnId: 'turn-one',
      occurredAt: 1,
      summary: null,
      body: {
        path: 'frontend/state.ts',
        action: 'created',
        state: 'succeeded',
        previousPath: null,
        linesAdded: 1,
        linesRemoved: 0,
        content: { text: 'const ready = true;', mediaType: 'text/plain' },
      },
    };

    expect(presentEntry(entry, new Map())).toMatchObject({
      kind: 'file',
      body: {
        kind: 'source',
        path: 'frontend/state.ts',
      },
    });
  });

  it('does not expose stored compaction text for an unsupported harness', () => {
    const entry: Entry = {
      type: 'compaction_finished',
      entryId: entryId('opaque-compaction'),
      cursor: 1,
      actorId: actorId('lead'),
      parentActorId: null,
      turnId: null,
      occurredAt: 1,
      summary: null,
      body: {
        beforeTokens: 100,
        afterTokens: 20,
        context: { text: 'obsolete placeholder', mediaType: 'text/markdown' },
      },
    };

    expect(presentEntry(entry, new Map(), new Map(), false)).toMatchObject({
      kind: 'block',
      header: { kind: 'note', label: 'Context compacted · 100 → 20 tokens' },
      body: { kind: 'empty' },
    });
  });

  it('exposes compacted context for a supporting harness', () => {
    const entry: Entry = {
      type: 'compaction_finished',
      entryId: entryId('readable-compaction'),
      cursor: 1,
      actorId: actorId('lead'),
      parentActorId: null,
      turnId: null,
      occurredAt: 1,
      summary: null,
      body: {
        beforeTokens: 100,
        afterTokens: 20,
        context: { text: 'Readable summary', mediaType: 'text/markdown' },
      },
    };

    expect(presentEntry(entry, new Map(), new Map(), true)).toMatchObject({
      kind: 'block',
      body: {
        kind: 'content',
        content: { text: 'Readable summary' },
      },
    });
  });

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

  it('marks reasoning as visually quiet thinking', () => {
    const entry: Entry = {
      type: 'reasoning',
      entryId: entryId('reasoning-one'),
      cursor: 1,
      actorId: actorId('lead'),
      parentActorId: null,
      turnId: 'turn-one',
      occurredAt: 1,
      summary: null,
      body: {
        reasoningId: 'reasoning-one',
        content: {
          text: '**Inspecting the frontend**',
          mediaType: 'text/markdown',
        },
      },
    };

    const presentation = presentEntry(entry, new Map());

    expect(presentation).toMatchObject({
      kind: 'message',
      className: 'thinking',
      conversationKind: 'message',
      label: 'lead',
    });
  });

  it('shows a browser interaction as an expandable tool block', () => {
    const entry: Entry = {
      type: 'browser',
      entryId: entryId('browser-refresh'),
      cursor: 2,
      actorId: actorId('lead'),
      parentActorId: null,
      turnId: 'turn-one',
      occurredAt: 2,
      summary: null,
      body: {
        action: 'Refresh the fixture application',
        state: 'succeeded',
        result: {
          text: '- banner:\n  - link "baqylau"',
          mediaType: 'text/plain',
        },
      },
    };

    expect(presentEntry(entry, new Map())).toMatchObject({
      kind: 'block',
      header: { kind: 'chip', label: 'Browser' },
      summary: 'Refresh the fixture application',
      summaryKind: 'tool',
      body: {
        kind: 'content',
        content: { text: '- banner:\n  - link "baqylau"' },
      },
    });
  });
});
