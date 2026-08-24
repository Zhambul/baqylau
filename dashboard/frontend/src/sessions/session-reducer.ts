import type { ActorId } from '../app/domain-ids';
import type { Entry } from '../entries/model';
import type { Actor } from './model';

const MAXIMUM_FEED_ENTRIES = 3000;

export function mergeActors(
  current: readonly Actor[],
  changed: readonly Actor[],
): readonly Actor[] {
  if (changed.length === 0) return current;
  const replacements = new Map(changed.map((actor) => [actor.actorId, actor]));
  const merged = current.map(
    (actor) => replacements.get(actor.actorId) ?? actor,
  );
  const known = new Set(current.map((actor) => actor.actorId));
  return [...merged, ...changed.filter((actor) => !known.has(actor.actorId))];
}

export function entriesForActor(
  entries: readonly Entry[],
  actorId: ActorId,
): readonly Entry[] {
  return entries.filter((entry) => entry.actorId === actorId);
}

function uniqueEntries(entries: readonly Entry[]): readonly Entry[] {
  const seen = new Set<string>();
  return entries.filter((entry) => {
    if (seen.has(entry.entryId)) return false;
    seen.add(entry.entryId);
    return true;
  });
}

export function initialEntriesNewestFirst(
  oldestFirst: readonly Entry[],
): readonly Entry[] {
  return uniqueEntries([...oldestFirst].reverse()).slice(
    0,
    MAXIMUM_FEED_ENTRIES,
  );
}

export function prependLiveEntries(
  currentNewestFirst: readonly Entry[],
  liveOldestFirst: readonly Entry[],
): readonly Entry[] {
  const known = new Set(currentNewestFirst.map((entry) => entry.entryId));
  const incoming = liveOldestFirst
    .filter((entry) => !known.has(entry.entryId))
    .reverse();
  return uniqueEntries([...incoming, ...currentNewestFirst]).slice(
    0,
    MAXIMUM_FEED_ENTRIES,
  );
}

export function appendOlderEntries(
  currentNewestFirst: readonly Entry[],
  olderOldestFirst: readonly Entry[],
): readonly Entry[] {
  const known = new Set(currentNewestFirst.map((entry) => entry.entryId));
  const older = olderOldestFirst
    .filter((entry) => !known.has(entry.entryId))
    .reverse();
  return uniqueEntries([...currentNewestFirst, ...older]).slice(
    0,
    MAXIMUM_FEED_ENTRIES,
  );
}
