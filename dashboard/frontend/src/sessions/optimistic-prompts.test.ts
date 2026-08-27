import { describe, expect, it } from 'vitest';

import {
  mergeQueuedPrompts,
  promptMatches,
  restoredPromptIsQueued,
} from './optimistic-prompts';

describe('promptMatches', () => {
  it('allows attachment and restored-draft prefixes', () => {
    expect(
      promptMatches('@/tmp/file previous textsend this', 'send this'),
    ).toBe(true);
  });

  it('never matches an empty send', () => {
    expect(promptMatches('anything', '')).toBe(false);
  });
});

describe('restoredPromptIsQueued', () => {
  it('does not restore a queued prompt after an interrupt starts it', () => {
    expect(
      restoredPromptIsQueued('next queued prompt', ['next queued prompt']),
    ).toBe(true);
  });

  it('keeps a different terminal draft', () => {
    expect(restoredPromptIsQueued('unfinished draft', ['next prompt'])).toBe(
      false,
    );
  });
});

describe('mergeQueuedPrompts', () => {
  it('does not duplicate an optimistic queue item persisted by the server', () => {
    expect(
      mergeQueuedPrompts(
        [{ requestId: 'one', text: '@/tmp/file queued message' }],
        [
          { requestId: 'one', text: 'queued message' },
          { requestId: 'two', text: 'another' },
        ],
      ),
    ).toEqual([
      { requestId: 'one', text: '@/tmp/file queued message' },
      { requestId: 'two', text: 'another' },
    ]);
  });

  it('drops persisted and optimistic queue items after live delivery', () => {
    expect(
      mergeQueuedPrompts(
        [
          { requestId: 'one', text: 'persisted message' },
          { requestId: 'two', text: 'keep persisted' },
        ],
        [
          { requestId: 'three', text: 'optimistic message' },
          { requestId: 'four', text: 'keep optimistic' },
        ],
        ['@/tmp/file persisted message', 'restored draft optimistic message'],
      ),
    ).toEqual([
      { requestId: 'two', text: 'keep persisted' },
      { requestId: 'four', text: 'keep optimistic' },
    ]);
  });

  it('consumes only one of two equal queued sends per delivered prompt', () => {
    expect(
      mergeQueuedPrompts(
        [
          { requestId: 'one', text: 'same' },
          { requestId: 'two', text: 'same' },
        ],
        [],
        ['same'],
      ),
    ).toEqual([{ requestId: 'two', text: 'same' }]);
  });
});
