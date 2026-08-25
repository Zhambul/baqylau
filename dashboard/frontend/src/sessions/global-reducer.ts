import type { SessionId } from '../app/domain-ids';
import type { GlobalStreamDelta } from '../api/stream-decoder';
import type { SessionSnapshot } from './model';

export type GlobalReduction = {
  readonly sessions: readonly SessionSnapshot[];
  readonly adopt: readonly SessionId[];
  readonly unknownActorSessions: readonly SessionId[];
};

export function reduceGlobalDelta(
  current: readonly SessionSnapshot[],
  delta: GlobalStreamDelta,
): GlobalReduction {
  const removed = new Set(
    delta.sessions
      .filter((changed) => changed.state !== 'running')
      .map((changed) => changed.sessionId),
  );
  const sessions = current.filter(
    (snapshot) => !removed.has(snapshot.session.sessionId),
  );
  const positions = new Map(
    sessions.map((snapshot, position) => [
      snapshot.session.sessionId,
      position,
    ]),
  );
  const adopt = new Set<SessionId>();
  const frameSessions = new Set<SessionId>();

  for (const changed of delta.sessions) {
    frameSessions.add(changed.sessionId);
    if (removed.has(changed.sessionId)) continue;
    const position = positions.get(changed.sessionId);
    if (position === undefined) {
      adopt.add(changed.sessionId);
      continue;
    }
    const existing = sessions[position];
    if (existing !== undefined) {
      sessions[position] = {
        ...existing,
        session: changed,
        live: changed.state === 'running',
      };
    }
  }

  const unknownActorSessions = new Set<SessionId>();
  for (const changed of delta.actors) {
    if (removed.has(changed.sessionId)) continue;
    const position = positions.get(changed.sessionId);
    if (position === undefined) {
      if (frameSessions.has(changed.sessionId)) {
        adopt.add(changed.sessionId);
      } else {
        unknownActorSessions.add(changed.sessionId);
      }
      continue;
    }
    const existing = sessions[position];
    if (existing === undefined) {
      continue;
    }
    const actors = [...existing.actors];
    const actorPosition = actors.findIndex(
      (actor) => actor.actorId === changed.actorId,
    );
    if (actorPosition === -1) {
      actors.push(changed);
    } else {
      actors[actorPosition] = changed;
    }
    sessions[position] = { ...existing, actors };
  }

  return {
    sessions,
    adopt: [...adopt],
    unknownActorSessions: [...unknownActorSessions],
  };
}
