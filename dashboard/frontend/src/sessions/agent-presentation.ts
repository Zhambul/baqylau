import type { Actor } from './model';

export type ActorStatePresentation = {
  readonly label: 'running' | 'done';
  readonly className: 'st-run' | 'st-ok';
};

export function actorState(actor: Actor): ActorStatePresentation {
  return actor.state === 'running'
    ? { label: 'running', className: 'st-run' }
    : { label: 'done', className: 'st-ok' };
}

export function actorGlyph(actor: Actor): '◈' | '◇' {
  return actor.role === 'teammate' ? '◈' : '◇';
}

export function actorDisplayName(actor: Actor): string {
  return (actor.description ?? actor.name) || actor.actorId;
}

export function actorEventCount(actor: Actor): number {
  return actor.statistics.toolCounts.reduce(
    (total, count) => total + count.count,
    0,
  );
}

function actorIsHusk(actor: Actor): boolean {
  return actor.description === null && actor.name === actor.actorId;
}

export function sortedChildActors(actors: readonly Actor[]): readonly Actor[] {
  return [...actors].sort(
    (left, right) =>
      Number(actorIsHusk(left)) - Number(actorIsHusk(right)) ||
      (left.startedAt ?? 0) - (right.startedAt ?? 0) ||
      left.actorId.localeCompare(right.actorId),
  );
}

export function actorCardIsHusk(actor: Actor): boolean {
  return actorIsHusk(actor);
}
