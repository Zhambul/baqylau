import type { components } from '../api/generated/schema';

type Schemas = components['schemas'];

export function wireActor(
  sessionId = 'session-one',
  actorId = 'actor-lead',
): Schemas['ActorResponse'] {
  return {
    session_id: sessionId,
    actor_id: actorId,
    parent_actor_id: null,
    role: 'lead',
    name: 'lead',
    description: null,
    state: 'running',
    started_at: 1_700_000_000,
    finished_at: null,
    model: 'opus',
    effort: 'high',
    status: 'working',
    usage: {
      tokens: {
        input_tokens: 100,
        output_tokens: 50,
        cache_read_tokens: 25,
        cache_write_tokens: 10,
        one_hour_cache_write_tokens: 5,
      },
      cost_in_usd: '0.42',
    },
    context: {
      used_tokens: 2_000,
      window_tokens: 10_000,
      compacting: false,
    },
    background: {
      running_shell_ids: ['shell-one'],
      monitor_count: 1,
      background_job_count: 2,
    },
    statistics: {
      prompt_count: 3,
      shell_command_count: 4,
      failed_shell_command_count: 1,
      file_count: 2,
      lines_added: 20,
      lines_removed: 5,
      actor_message_count: 1,
      tool_counts: [{ tool: 'Read', count: 2 }],
      active_seconds: 120,
      active: true,
    },
  };
}

export function wireSession(
  id = 'session-one',
  directory = '/work/project',
): Schemas['SessionResponse'] {
  return {
    session_id: id,
    harness: 'claude-code',
    title: 'Fix the dashboard',
    state: 'running',
    working_directory: directory,
    started_at: 1_700_000_000,
    finished_at: null,
    account: { account_id: 'account-one', display_name: 'Personal' },
    lead_actor_id: 'actor-lead',
    goal: { objective: 'Ship it', completed: false },
    tasks: [
      {
        task_id: 'task-one',
        subject: 'Rewrite',
        description: null,
        state: 'in_progress',
        owner_actor_id: 'actor-lead',
      },
    ],
  };
}

export function wireSnapshot(
  id = 'session-one',
  directory = '/work/project',
): Schemas['SessionDataResponse'] {
  return {
    cursor: 42,
    session: wireSession(id, directory),
    actors: [wireActor(id)],
    live: true,
    repository: { branch: 'main', worktree: null, dirty: true },
  };
}

export function wireEntry(
  cursor: number,
  actor = 'actor-lead',
): Schemas['EntryResponse'] {
  const suffix = String(cursor);
  return {
    entry_id: `entry-${suffix}`,
    type: 'message',
    cursor,
    actor_id: actor,
    parent_actor_id: null,
    turn_id: `turn-${suffix}`,
    occurred_at: 1_700_000_000 + cursor,
    summary: null,
    body: {
      message_id: `message-${suffix}`,
      role: 'assistant',
      phase: 'end_turn',
      content: { text: `message ${suffix}`, media_type: 'text/markdown' },
      recipient_actor_id: null,
      reply_to: null,
    },
  };
}
