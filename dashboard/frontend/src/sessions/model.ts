import type { ActorId, SessionId, TaskId } from '../app/domain-ids';

type LifecycleState = 'running' | 'finished';
type ActorRole = 'lead' | 'child' | 'teammate' | 'sidecar';
export type ActorStatus =
  | 'idle'
  | 'thinking'
  | 'working'
  | 'executing'
  | 'awaiting_background'
  | 'awaiting_attention'
  | 'awaiting_response';
type TaskState = 'pending' | 'in_progress' | 'completed' | 'deleted';
type GoalState =
  | 'active'
  | 'paused'
  | 'blocked'
  | 'usage_limited'
  | 'budget_limited'
  | 'completed'
  | 'cleared';

export type TokenUsage = {
  readonly inputTokens: number;
  readonly outputTokens: number;
  readonly cacheReadTokens: number;
  readonly cacheWriteTokens: number;
  readonly oneHourCacheWriteTokens: number;
};

type ActorStatistics = {
  readonly promptCount: number;
  readonly shellCommandCount: number;
  readonly failedShellCommandCount: number;
  readonly fileCount: number;
  readonly linesAdded: number;
  readonly linesRemoved: number;
  readonly actorMessageCount: number;
  readonly toolCounts: readonly {
    readonly tool: string;
    readonly count: number;
  }[];
  readonly activeSeconds: number;
  readonly active: boolean;
};

export type Actor = {
  readonly sessionId: SessionId;
  readonly actorId: ActorId;
  readonly parentActorId: ActorId | null;
  readonly role: ActorRole;
  readonly name: string;
  readonly description: string | null;
  readonly state: LifecycleState;
  readonly startedAt: number | null;
  readonly finishedAt: number | null;
  readonly model: string | null;
  readonly effort: string | null;
  readonly status: ActorStatus | null;
  readonly usage: {
    readonly tokens: TokenUsage;
    readonly costInUsd: string | null;
  };
  readonly context: {
    readonly usedTokens: number;
    readonly windowTokens: number;
    readonly compacting: boolean;
  };
  readonly background: {
    readonly runningShellIds: readonly string[];
    readonly monitorCount: number;
    readonly backgroundJobCount: number;
  };
  readonly statistics: ActorStatistics;
};

export type Session = {
  readonly sessionId: SessionId;
  readonly harness: string;
  readonly title: string | null;
  readonly state: LifecycleState;
  readonly workingDirectory: string;
  readonly startedAt: number | null;
  readonly finishedAt: number | null;
  readonly account: {
    readonly accountId: string;
    readonly displayName: string;
  } | null;
  readonly leadActorId: ActorId;
  readonly goal: {
    readonly objective: string | null;
    readonly state: GoalState;
    readonly reason: string | null;
    readonly completed: boolean;
  } | null;
  readonly tasks: readonly {
    readonly taskId: TaskId;
    readonly subject: string;
    readonly description: string | null;
    readonly state: TaskState;
    readonly ownerActorId: ActorId | null;
  }[];
};

export type SessionSnapshot = {
  readonly cursor: number;
  readonly session: Session;
  readonly actors: readonly Actor[];
  readonly live: boolean;
  readonly projectDirectory: string;
  readonly repository: {
    readonly branch: string;
    readonly worktree: string | null;
    readonly dirty: boolean;
  } | null;
};

export type SessionList = {
  readonly cursor: number;
  readonly sessions: readonly SessionSnapshot[];
};

export function leadActor(snapshot: SessionSnapshot): Actor | null {
  return (
    snapshot.actors.find(
      (actor) => actor.actorId === snapshot.session.leadActorId,
    ) ?? null
  );
}

export function lastActiveAt(snapshot: SessionSnapshot): number {
  return snapshot.session.finishedAt ?? snapshot.session.startedAt ?? 0;
}
