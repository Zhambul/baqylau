import type { Entry } from '../entries/model';

export type QueuedPrompt = {
  readonly requestId: string;
  readonly text: string;
};

export function deliveredPrompt(entry: Entry): string | null {
  return entry.type === 'message' &&
    entry.body.role === 'user' &&
    entry.body.phase === 'prompt'
    ? entry.body.content.text.trim()
    : null;
}

export function promptMatches(delivered: string, sent: string): boolean {
  return sent.length > 0 && delivered.endsWith(sent);
}

export function mergeQueuedPrompts(
  persisted: readonly QueuedPrompt[],
  optimistic: readonly QueuedPrompt[],
  delivered: readonly string[] = [],
): readonly QueuedPrompt[] {
  const persistedIds = new Set(persisted.map((item) => item.requestId));
  const merged = [
    ...persisted,
    ...optimistic.filter((item) => !persistedIds.has(item.requestId)),
  ];
  const unmatched = [...delivered];
  return merged.filter((item) => {
    const match = unmatched.findIndex((prompt) =>
      promptMatches(prompt, item.text),
    );
    if (match < 0) return true;
    unmatched.splice(match, 1);
    return false;
  });
}
