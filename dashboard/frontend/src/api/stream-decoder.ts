import type { Entry } from '../entries/model';
import type { Actor, Session } from '../sessions/model';
import type { components } from './generated/schema';
import { decodeEntry } from './translators/entries';
import { translateActor, translateSession } from './translators/session-data';

type Schemas = components['schemas'];

export class StreamValidationFailure extends Error {
  readonly kind = 'validation';

  constructor(message: string) {
    super(message);
    this.name = 'StreamValidationFailure';
  }
}

function parsedJson(text: string): unknown {
  try {
    const value: unknown = JSON.parse(text);
    return value;
  } catch (error) {
    throw new StreamValidationFailure(
      `event data is not JSON: ${error instanceof Error ? error.message : String(error)}`,
    );
  }
}

function field(value: unknown, name: string): unknown {
  if (typeof value !== 'object' || value === null || !(name in value)) {
    throw new StreamValidationFailure(`event field is missing: ${name}`);
  }
  return Reflect.get(value, name);
}

function stringValue(value: unknown, name: string): string {
  const candidate = field(value, name);
  if (typeof candidate !== 'string') {
    throw new StreamValidationFailure(`event field must be a string: ${name}`);
  }
  return candidate;
}

function nullableString(value: unknown, name: string): string | null {
  const candidate = field(value, name);
  if (candidate === null || typeof candidate === 'string') {
    return candidate;
  }
  throw new StreamValidationFailure(
    `event field must be a string or null: ${name}`,
  );
}

function numberValue(value: unknown, name: string): number {
  const candidate = field(value, name);
  if (typeof candidate !== 'number' || !Number.isFinite(candidate)) {
    throw new StreamValidationFailure(
      `event field must be a finite number: ${name}`,
    );
  }
  return candidate;
}

function nullableNumber(value: unknown, name: string): number | null {
  const candidate = field(value, name);
  if (candidate === null) {
    return null;
  }
  if (typeof candidate === 'number' && Number.isFinite(candidate)) {
    return candidate;
  }
  throw new StreamValidationFailure(
    `event field must be a finite number or null: ${name}`,
  );
}

function booleanValue(value: unknown, name: string): boolean {
  const candidate = field(value, name);
  if (typeof candidate !== 'boolean') {
    throw new StreamValidationFailure(`event field must be a boolean: ${name}`);
  }
  return candidate;
}

function arrayValue(value: unknown, name: string): readonly unknown[] {
  const candidate = field(value, name);
  if (!Array.isArray(candidate)) {
    throw new StreamValidationFailure(`event field must be an array: ${name}`);
  }
  return candidate;
}

function lifecycleState(
  value: unknown,
  name: string,
): Schemas['LifecycleState'] {
  const candidate = stringValue(value, name);
  switch (candidate) {
    case 'running':
    case 'finished':
      return candidate;
    default:
      throw new StreamValidationFailure(
        `event field has an unknown lifecycle state: ${name}`,
      );
  }
}

function actorRole(value: unknown, name: string): Schemas['ActorRole'] {
  const candidate = stringValue(value, name);
  switch (candidate) {
    case 'lead':
    case 'child':
    case 'teammate':
    case 'sidecar':
      return candidate;
    default:
      throw new StreamValidationFailure(
        `event field has an unknown actor role: ${name}`,
      );
  }
}

function actorStatus(
  value: unknown,
  name: string,
): Schemas['ActorStatus'] | null {
  const candidate = nullableString(value, name);
  switch (candidate) {
    case null:
    case 'idle':
    case 'thinking':
    case 'working':
    case 'executing':
    case 'awaiting_background':
    case 'awaiting_attention':
    case 'awaiting_response':
      return candidate;
    default:
      throw new StreamValidationFailure(
        `event field has an unknown actor status: ${name}`,
      );
  }
}

function taskState(value: unknown, name: string): Schemas['TaskState'] {
  const candidate = stringValue(value, name);
  switch (candidate) {
    case 'pending':
    case 'in_progress':
    case 'completed':
    case 'deleted':
      return candidate;
    default:
      throw new StreamValidationFailure(
        `event field has an unknown task state: ${name}`,
      );
  }
}

function goalState(value: unknown, name: string): Schemas['GoalState'] {
  const candidate = stringValue(value, name);
  switch (candidate) {
    case 'active':
    case 'paused':
    case 'blocked':
    case 'usage_limited':
    case 'budget_limited':
    case 'completed':
    case 'cleared':
      return candidate;
    default:
      throw new StreamValidationFailure(
        `event field has an unknown goal state: ${name}`,
      );
  }
}

function decodeSession(value: unknown): Schemas['SessionResponse'] {
  const accountValue = field(value, 'account');
  const goalValue = field(value, 'goal');
  return {
    session_id: stringValue(value, 'session_id'),
    harness: stringValue(value, 'harness'),
    title: nullableString(value, 'title'),
    state: lifecycleState(value, 'state'),
    working_directory: stringValue(value, 'working_directory'),
    started_at: nullableNumber(value, 'started_at'),
    finished_at: nullableNumber(value, 'finished_at'),
    account:
      accountValue === null
        ? null
        : {
            account_id: stringValue(accountValue, 'account_id'),
            display_name: stringValue(accountValue, 'display_name'),
          },
    lead_actor_id: stringValue(value, 'lead_actor_id'),
    goal:
      goalValue === null
        ? null
        : {
            objective: nullableString(goalValue, 'objective'),
            state: goalState(goalValue, 'state'),
            reason: nullableString(goalValue, 'reason'),
            completed: booleanValue(goalValue, 'completed'),
          },
    tasks: arrayValue(value, 'tasks').map((task) => ({
      task_id: stringValue(task, 'task_id'),
      subject: stringValue(task, 'subject'),
      description: nullableString(task, 'description'),
      state: taskState(task, 'state'),
      owner_actor_id: nullableString(task, 'owner_actor_id'),
    })),
  };
}

function decodeTokens(value: unknown): Schemas['TokenUsageResponse'] {
  return {
    input_tokens: numberValue(value, 'input_tokens'),
    output_tokens: numberValue(value, 'output_tokens'),
    cache_read_tokens: numberValue(value, 'cache_read_tokens'),
    cache_write_tokens: numberValue(value, 'cache_write_tokens'),
    one_hour_cache_write_tokens: numberValue(
      value,
      'one_hour_cache_write_tokens',
    ),
  };
}

function decodeActor(value: unknown): Schemas['ActorResponse'] {
  const usage = field(value, 'usage');
  const context = field(value, 'context');
  const background = field(value, 'background');
  const statistics = field(value, 'statistics');
  return {
    session_id: stringValue(value, 'session_id'),
    actor_id: stringValue(value, 'actor_id'),
    parent_actor_id: nullableString(value, 'parent_actor_id'),
    role: actorRole(value, 'role'),
    name: stringValue(value, 'name'),
    description: nullableString(value, 'description'),
    state: lifecycleState(value, 'state'),
    started_at: nullableNumber(value, 'started_at'),
    finished_at: nullableNumber(value, 'finished_at'),
    model: nullableString(value, 'model'),
    effort: nullableString(value, 'effort'),
    status: actorStatus(value, 'status'),
    usage: {
      tokens: decodeTokens(field(usage, 'tokens')),
      cost_in_usd: nullableString(usage, 'cost_in_usd'),
    },
    context: {
      used_tokens: numberValue(context, 'used_tokens'),
      window_tokens: numberValue(context, 'window_tokens'),
      compacting: booleanValue(context, 'compacting'),
    },
    background: {
      running_shell_ids: arrayValue(background, 'running_shell_ids').map(
        (shellId) => {
          if (typeof shellId !== 'string') {
            throw new StreamValidationFailure(
              'event running shell id must be a string',
            );
          }
          return shellId;
        },
      ),
      monitor_count: numberValue(background, 'monitor_count'),
      background_job_count: numberValue(background, 'background_job_count'),
    },
    statistics: {
      prompt_count: numberValue(statistics, 'prompt_count'),
      shell_command_count: numberValue(statistics, 'shell_command_count'),
      failed_shell_command_count: numberValue(
        statistics,
        'failed_shell_command_count',
      ),
      file_count: numberValue(statistics, 'file_count'),
      lines_added: numberValue(statistics, 'lines_added'),
      lines_removed: numberValue(statistics, 'lines_removed'),
      actor_message_count: numberValue(statistics, 'actor_message_count'),
      tool_counts: arrayValue(statistics, 'tool_counts').map((count) => ({
        tool: stringValue(count, 'tool'),
        count: numberValue(count, 'count'),
      })),
      active_seconds: numberValue(statistics, 'active_seconds'),
      active: booleanValue(statistics, 'active'),
    },
  };
}

export type GlobalStreamDelta = {
  readonly sessions: readonly Session[];
  readonly actors: readonly Actor[];
};

export type SessionStreamDelta = {
  readonly session: Session | null;
  readonly actors: readonly Actor[];
  readonly entries: readonly Entry[];
};

export function decodeReadyFrame(text: string): string {
  return stringValue(parsedJson(text), 'boot_id');
}

export function decodeGlobalStreamFrame(text: string): GlobalStreamDelta {
  const value = parsedJson(text);
  return {
    sessions: arrayValue(value, 'sessions').map((session) =>
      translateSession(decodeSession(session)),
    ),
    actors: arrayValue(value, 'actors').map((actor) =>
      translateActor(decodeActor(actor)),
    ),
  };
}

export function decodeSessionStreamFrame(text: string): SessionStreamDelta {
  const value = parsedJson(text);
  const session = field(value, 'session');
  return {
    session: session === null ? null : translateSession(decodeSession(session)),
    actors: arrayValue(value, 'actors').map((actor) =>
      translateActor(decodeActor(actor)),
    ),
    entries: arrayValue(value, 'entries').map(decodeEntry),
  };
}
