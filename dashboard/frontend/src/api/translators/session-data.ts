import { actorId, sessionId, taskId } from '../../app/domain-ids';
import type {
  Actor,
  Session,
  SessionList,
  SessionSnapshot,
  TokenUsage,
} from '../../sessions/model';
import type { components } from '../generated/schema';

type Schemas = components['schemas'];
type WireActor = Schemas['ActorResponse'];
type WireSession = Schemas['SessionResponse'];
type WireSnapshot = Schemas['SessionDataResponse'];

function tokens(wire: Schemas['TokenUsageResponse']): TokenUsage {
  return {
    inputTokens: wire.input_tokens,
    outputTokens: wire.output_tokens,
    cacheReadTokens: wire.cache_read_tokens,
    cacheWriteTokens: wire.cache_write_tokens,
    oneHourCacheWriteTokens: wire.one_hour_cache_write_tokens,
  };
}

export function translateActor(wire: WireActor): Actor {
  return {
    sessionId: sessionId(wire.session_id),
    actorId: actorId(wire.actor_id),
    parentActorId:
      wire.parent_actor_id === null ? null : actorId(wire.parent_actor_id),
    role: wire.role,
    name: wire.name,
    description: wire.description,
    state: wire.state,
    startedAt: wire.started_at,
    finishedAt: wire.finished_at,
    model: wire.model,
    effort: wire.effort,
    status: wire.status,
    usage: {
      tokens: tokens(wire.usage.tokens),
      costInUsd: wire.usage.cost_in_usd,
    },
    context: {
      usedTokens: wire.context.used_tokens,
      windowTokens: wire.context.window_tokens,
      compacting: wire.context.compacting,
    },
    background: {
      runningShellIds: wire.background.running_shell_ids,
      monitorCount: wire.background.monitor_count,
      backgroundJobCount: wire.background.background_job_count,
    },
    statistics: {
      promptCount: wire.statistics.prompt_count,
      shellCommandCount: wire.statistics.shell_command_count,
      failedShellCommandCount: wire.statistics.failed_shell_command_count,
      fileCount: wire.statistics.file_count,
      linesAdded: wire.statistics.lines_added,
      linesRemoved: wire.statistics.lines_removed,
      actorMessageCount: wire.statistics.actor_message_count,
      toolCounts: wire.statistics.tool_counts.map((count) => ({
        tool: count.tool,
        count: count.count,
      })),
      activeSeconds: wire.statistics.active_seconds,
      active: wire.statistics.active,
    },
  };
}

export function translateSession(wire: WireSession): Session {
  return {
    sessionId: sessionId(wire.session_id),
    harness: wire.harness,
    title: wire.title,
    state: wire.state,
    workingDirectory: wire.working_directory,
    startedAt: wire.started_at,
    finishedAt: wire.finished_at,
    account:
      wire.account === null
        ? null
        : {
            accountId: wire.account.account_id,
            displayName: wire.account.display_name,
          },
    leadActorId: actorId(wire.lead_actor_id),
    goal:
      wire.goal === null
        ? null
        : {
            objective: wire.goal.objective,
            state: wire.goal.state,
            reason: wire.goal.reason,
            completed: wire.goal.completed,
          },
    tasks: wire.tasks.map((task) => ({
      taskId: taskId(task.task_id),
      subject: task.subject,
      description: task.description,
      state: task.state,
      ownerActorId:
        task.owner_actor_id === null ? null : actorId(task.owner_actor_id),
    })),
  };
}

export function translateSessionSnapshot(wire: WireSnapshot): SessionSnapshot {
  return {
    cursor: wire.cursor,
    session: translateSession(wire.session),
    actors: wire.actors.map(translateActor),
    live: wire.live,
    projectDirectory: wire.project_directory,
    repository:
      wire.repository === null
        ? null
        : {
            branch: wire.repository.branch,
            worktree: wire.repository.worktree,
            dirty: wire.repository.dirty,
          },
  };
}

export function translateSessionList(
  wire: Schemas['SessionDataListResponse'],
): SessionList {
  return {
    cursor: wire.cursor,
    sessions: wire.sessions.map(translateSessionSnapshot),
  };
}
