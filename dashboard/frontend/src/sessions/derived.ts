import type { ActorStatus, SessionSnapshot, TokenUsage } from './model';
import { leadActor } from './model';

const EMPTY_TOKENS: TokenUsage = {
  inputTokens: 0,
  outputTokens: 0,
  cacheReadTokens: 0,
  cacheWriteTokens: 0,
  oneHourCacheWriteTokens: 0,
};

export const STATUS_LABELS: ReadonlyMap<ActorStatus, string> = new Map([
  ['idle', 'idle'],
  ['thinking', 'busy'],
  ['working', 'busy'],
  ['executing', 'running'],
  ['awaiting_background', 'running'],
  ['awaiting_attention', 'asking you'],
  ['awaiting_response', 'your turn'],
]);

export function sessionStatus(snapshot: SessionSnapshot): ActorStatus | null {
  return leadActor(snapshot)?.status ?? null;
}

export function sessionTokenUsage(snapshot: SessionSnapshot): TokenUsage {
  return snapshot.actors.reduce<TokenUsage>(
    (sum, actor) => ({
      inputTokens: sum.inputTokens + actor.usage.tokens.inputTokens,
      outputTokens: sum.outputTokens + actor.usage.tokens.outputTokens,
      cacheReadTokens: sum.cacheReadTokens + actor.usage.tokens.cacheReadTokens,
      cacheWriteTokens:
        sum.cacheWriteTokens + actor.usage.tokens.cacheWriteTokens,
      oneHourCacheWriteTokens:
        sum.oneHourCacheWriteTokens +
        actor.usage.tokens.oneHourCacheWriteTokens,
    }),
    EMPTY_TOKENS,
  );
}

export function tokenCount(tokens: TokenUsage): number {
  return (
    tokens.inputTokens +
    tokens.outputTokens +
    tokens.cacheReadTokens +
    tokens.cacheWriteTokens +
    tokens.oneHourCacheWriteTokens
  );
}

export function sessionCost(snapshot: SessionSnapshot): number | null {
  let total: number | null = null;
  for (const actor of snapshot.actors) {
    if (actor.usage.costInUsd === null) {
      continue;
    }
    const cost = Number(actor.usage.costInUsd);
    if (Number.isFinite(cost)) {
      total = (total ?? 0) + cost;
    }
  }
  return total;
}

export function directoryName(path: string): string {
  const segments = path.split('/').filter((segment) => segment.length > 0);
  return segments.at(-1) ?? '';
}
