import type { Entry } from '../entries/model';
import type { GlobalApplication } from '../application/model';
import type { SessionApplication } from '../application/session-model';
import type { Actor, Session } from '../sessions/model';
import type { components } from './generated/schema';
import { decodeEntry } from './translators/entries';
import { translateGlobalApplication } from './translators/application';
import { translateSessionApplication } from './translators/session-application';
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

function optionalNullableString(value: unknown, name: string): string | null {
  if (
    typeof value === 'object' &&
    value !== null &&
    (!(name in value) || Reflect.get(value, name) === null)
  ) {
    return null;
  }
  return stringValue(value, name);
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

function viewMode(value: unknown, name: string): Schemas['ViewMode'] {
  const candidate = stringValue(value, name);
  if (
    candidate === 'verbose' ||
    candidate === 'default' ||
    candidate === 'focus'
  )
    return candidate;
  throw new StreamValidationFailure(
    `event field has an unknown view mode: ${name}`,
  );
}

function decodeSessionApplication(
  value: unknown,
): Schemas['SessionApplicationResponse'] {
  const preferences = field(value, 'preferences');
  const composer = field(value, 'composer');
  const composerDraft = field(composer, 'draft');
  const queue = field(composer, 'queue');
  const dialog = field(value, 'dialog');
  const dialogDraft = field(dialog, 'draft');
  const terminal = field(value, 'terminal');
  const inputState = field(terminal, 'input_state');
  return {
    preferences: {
      view_mode: viewMode(preferences, 'view_mode'),
      notifications_muted: booleanValue(preferences, 'notifications_muted'),
      tasks_hidden: booleanValue(preferences, 'tasks_hidden'),
    },
    composer: {
      draft:
        composerDraft === null
          ? null
          : {
              text: stringValue(composerDraft, 'text'),
              origin: stringValue(composerDraft, 'origin'),
              sequence: numberValue(composerDraft, 'sequence'),
            },
      queue:
        queue === null
          ? null
          : {
              items: arrayValue(queue, 'items').map((item) => ({
                request_id: stringValue(item, 'request_id'),
                text: stringValue(item, 'text'),
              })),
              origin: stringValue(queue, 'origin'),
            },
    },
    dialog: {
      draft:
        dialogDraft === null
          ? null
          : {
              attention_id: stringValue(dialogDraft, 'attention_id'),
              answers: arrayValue(dialogDraft, 'answers').map((answer) => ({
                selected: arrayValue(answer, 'selected').map((item) => {
                  if (typeof item !== 'string')
                    throw new StreamValidationFailure(
                      'event answer selection must be a string',
                    );
                  return item;
                }),
                other: stringValue(answer, 'other'),
              })),
              origin: stringValue(dialogDraft, 'origin'),
            },
    },
    terminal: {
      window_id: nullableString(terminal, 'window_id'),
      input_state:
        inputState === null
          ? null
          : {
              typed_text: nullableString(inputState, 'typed_text'),
              suggestion: nullableString(inputState, 'suggestion'),
            },
    },
    errors: arrayValue(value, 'errors').map((error) => ({
      error_id: numberValue(error, 'error_id'),
      timestamp: numberValue(error, 'timestamp'),
      component: stringValue(error, 'component'),
      action: stringValue(error, 'action'),
      traceback: stringValue(error, 'traceback'),
      context: stringValue(error, 'context'),
    })),
  };
}

export function decodeSessionApplicationFrame(
  text: string,
): SessionApplication {
  return translateSessionApplication(
    decodeSessionApplication(parsedJson(text)),
  );
}

function numberRecord(value: unknown, name: string): Record<string, number> {
  const candidate = field(value, name);
  if (typeof candidate !== 'object' || candidate === null) {
    throw new StreamValidationFailure(`event field must be an object: ${name}`);
  }
  const entries = Object.entries(candidate);
  if (
    entries.some(
      ([, item]) => typeof item !== 'number' || !Number.isFinite(item),
    )
  ) {
    throw new StreamValidationFailure(
      `event field values must be finite numbers: ${name}`,
    );
  }
  return Object.fromEntries(entries);
}

function usageScope(value: unknown, name: string): Schemas['UsageWindowScope'] {
  const candidate = stringValue(value, name);
  if (candidate === 'account' || candidate === 'model') return candidate;
  throw new StreamValidationFailure(
    `event field has an unknown usage scope: ${name}`,
  );
}

function decodeUsageRow(value: unknown): Schemas['UsageRowResponse'] {
  const limit = field(value, 'limit');
  return {
    harness: stringValue(value, 'harness'),
    account_id: nullableString(value, 'account_id'),
    display_name: stringValue(value, 'display_name'),
    switchable: booleanValue(value, 'switchable'),
    default_for_launch: booleanValue(value, 'default_for_launch'),
    plan: nullableString(value, 'plan'),
    windows: arrayValue(value, 'windows').map((window) => ({
      key: stringValue(window, 'key'),
      label: stringValue(window, 'label'),
      used_percent: stringValue(window, 'used_percent'),
      resets_at: nullableNumber(window, 'resets_at'),
      duration_minutes: nullableNumber(window, 'duration_minutes'),
      scope: usageScope(window, 'scope'),
      model_id: nullableString(window, 'model_id'),
    })),
    scheduling_score: nullableString(value, 'scheduling_score'),
    scheduling_allowed: booleanValue(value, 'scheduling_allowed'),
    limit:
      limit === null
        ? null
        : {
            model_id: nullableString(limit, 'model_id'),
            message: nullableString(limit, 'message'),
            resets_at: nullableNumber(limit, 'resets_at'),
          },
    authentication_error: nullableString(value, 'authentication_error'),
    collection_error: optionalNullableString(value, 'collection_error'),
  };
}

function decodeGlobalApplication(
  value: unknown,
): Schemas['GlobalApplicationResponse'] {
  const notifications = field(value, 'notifications');
  const latest = field(notifications, 'latest');
  const preferences = field(value, 'preferences');
  const newSession = field(preferences, 'new_session');
  const limits = field(preferences, 'limits');
  return {
    usage_rows: arrayValue(value, 'usage_rows').map(decodeUsageRow),
    notifications: {
      enabled: booleanValue(notifications, 'enabled'),
      latest:
        latest === null
          ? null
          : {
              revision: numberValue(latest, 'revision'),
              session_id: stringValue(latest, 'session_id'),
              kind: stringValue(latest, 'kind'),
              project: stringValue(latest, 'project'),
              title: stringValue(latest, 'title'),
            },
    },
    preferences: {
      new_session: {
        working_directory: nullableString(newSession, 'working_directory'),
        harness: nullableString(newSession, 'harness'),
        model: nullableString(newSession, 'model'),
        effort: nullableString(newSession, 'effort'),
      },
      new_session_drafts: arrayValue(preferences, 'new_session_drafts').map(
        (draft) => ({
          working_directory: stringValue(draft, 'working_directory'),
          text: stringValue(draft, 'text'),
          sequence: numberValue(draft, 'sequence'),
        }),
      ),
      hidden_directories: numberRecord(preferences, 'hidden_directories'),
      limits: {
        upload_bytes: numberValue(limits, 'upload_bytes'),
        rename_characters: numberValue(limits, 'rename_characters'),
        presence_seconds: numberValue(limits, 'presence_seconds'),
      },
    },
  };
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

export function decodeGlobalApplicationFrame(text: string): GlobalApplication {
  return translateGlobalApplication(decodeGlobalApplication(parsedJson(text)));
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
