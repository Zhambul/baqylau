import type { Entry } from '../entries/model';

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

export function mergeQueuedPromptTexts(
  persisted: readonly string[],
  optimistic: readonly string[],
  delivered: readonly string[] = [],
): readonly string[] {
  const merged = [
    ...persisted,
    ...optimistic.filter(
      (text) =>
        !persisted.some((known) => known === text || known.endsWith(text)),
    ),
  ];
  return merged.filter(
    (text) => !delivered.some((prompt) => promptMatches(prompt, text)),
  );
}
