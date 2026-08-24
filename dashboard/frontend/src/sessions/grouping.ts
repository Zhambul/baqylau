import type { SessionSnapshot } from './model';
import { lastActiveAt } from './model';

const ARCHIVE_SECONDS = 3 * 24 * 60 * 60;

export type SessionGroup = {
  readonly workingDirectory: string;
  readonly count: number;
  readonly active: readonly SessionSnapshot[];
  readonly parked: readonly SessionSnapshot[];
  readonly archived: readonly SessionSnapshot[];
};

function orderKey(snapshot: SessionSnapshot): number {
  return snapshot.session.startedAt ?? lastActiveAt(snapshot);
}

function isHidden(
  directory: string,
  sessions: readonly SessionSnapshot[],
  hiddenDirectories: ReadonlyMap<string, number>,
): boolean {
  const hiddenAt = hiddenDirectories.get(directory);
  if (hiddenAt === undefined) {
    return false;
  }
  return !sessions.some(
    (snapshot) => snapshot.live || (snapshot.session.startedAt ?? 0) > hiddenAt,
  );
}

export function groupSessions(
  sessions: readonly SessionSnapshot[],
  hiddenDirectories: ReadonlyMap<string, number>,
  now = Date.now() / 1_000,
): readonly SessionGroup[] {
  const byDirectory = new Map<string, SessionSnapshot[]>();
  for (const snapshot of sessions) {
    const directory = snapshot.session.workingDirectory;
    const group = byDirectory.get(directory) ?? [];
    group.push(snapshot);
    byDirectory.set(directory, group);
  }

  return [...byDirectory.entries()]
    .sort(
      ([, first], [, second]) =>
        Math.max(...second.map(orderKey)) - Math.max(...first.map(orderKey)),
    )
    .filter(
      ([directory, group]) => !isHidden(directory, group, hiddenDirectories),
    )
    .map(([workingDirectory, group]) => {
      const active = group.filter((snapshot) => snapshot.live);
      const inactive = group.filter((snapshot) => !snapshot.live);
      return {
        workingDirectory,
        count: group.length,
        active,
        parked: inactive.filter(
          (snapshot) => now - lastActiveAt(snapshot) <= ARCHIVE_SECONDS,
        ),
        archived: inactive.filter(
          (snapshot) => now - lastActiveAt(snapshot) > ARCHIVE_SECONDS,
        ),
      };
    });
}
