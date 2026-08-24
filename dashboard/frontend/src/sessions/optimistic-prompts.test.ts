import { describe, expect, it } from 'vitest';

import { mergeQueuedPromptTexts, promptMatches } from './optimistic-prompts';

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

describe('mergeQueuedPromptTexts', () => {
  it('does not duplicate an optimistic queue item persisted by the server', () => {
    expect(
      mergeQueuedPromptTexts(
        ['@/tmp/file queued message'],
        ['queued message', 'another'],
      ),
    ).toEqual(['@/tmp/file queued message', 'another']);
  });
});
