import { actorId, entryId } from '../../app/domain-ids';
import type {
  Content,
  Entry,
  EntryContent,
  EntryPage,
  Question,
} from '../../entries/model';
import type { components } from '../generated/schema';

type Schemas = components['schemas'];

class EntryValidationFailure extends Error {
  readonly kind = 'validation';

  constructor(message: string) {
    super(message);
    this.name = 'EntryValidationFailure';
  }
}

function field(value: unknown, name: string): unknown {
  if (typeof value !== 'object' || value === null || !(name in value)) {
    throw new EntryValidationFailure(`entry field is missing: ${name}`);
  }
  return Reflect.get(value, name);
}

function text(value: unknown, name: string): string {
  const candidate = field(value, name);
  if (typeof candidate !== 'string') {
    throw new EntryValidationFailure(`entry field must be a string: ${name}`);
  }
  return candidate;
}

function optionalText(value: unknown, name: string): string | null {
  const candidate = field(value, name);
  if (candidate === null || typeof candidate === 'string') return candidate;
  throw new EntryValidationFailure(
    `entry field must be a string or null: ${name}`,
  );
}

function finiteNumber(value: unknown, name: string): number {
  const candidate = field(value, name);
  if (typeof candidate !== 'number' || !Number.isFinite(candidate)) {
    throw new EntryValidationFailure(
      `entry field must be a finite number: ${name}`,
    );
  }
  return candidate;
}

function optionalCount(value: unknown, name: string): number | null {
  const candidate = field(value, name);
  if (candidate === null) return null;
  if (typeof candidate === 'number' && Number.isFinite(candidate))
    return candidate;
  throw new EntryValidationFailure(
    `entry field must be a number or null: ${name}`,
  );
}

function flag(value: unknown, name: string): boolean {
  const candidate = field(value, name);
  if (typeof candidate !== 'boolean') {
    throw new EntryValidationFailure(`entry field must be a boolean: ${name}`);
  }
  return candidate;
}

function list(value: unknown, name: string): readonly unknown[] {
  const candidate = field(value, name);
  if (!Array.isArray(candidate)) {
    throw new EntryValidationFailure(`entry field must be an array: ${name}`);
  }
  return candidate;
}

function content(value: unknown): Content {
  const mediaType = text(value, 'media_type');
  if (mediaType !== 'text/plain' && mediaType !== 'text/markdown') {
    throw new EntryValidationFailure('entry has an unknown content media type');
  }
  return { text: text(value, 'text'), mediaType };
}

function optionalContent(value: unknown, name: string): Content | null {
  const candidate = field(value, name);
  return candidate === null ? null : content(candidate);
}

function oneOf<Value extends string>(
  value: unknown,
  name: string,
  choices: readonly Value[],
): Value {
  const candidate = text(value, name);
  const match = choices.find((choice) => choice === candidate);
  if (match === undefined) {
    throw new EntryValidationFailure(
      `entry field has an unknown value: ${name}`,
    );
  }
  return match;
}

function stringList(value: unknown, name: string): readonly string[] {
  return list(value, name).map((item) => {
    if (typeof item !== 'string') {
      throw new EntryValidationFailure(
        `entry list must contain strings: ${name}`,
      );
    }
    return item;
  });
}

function questions(value: unknown): readonly Question[] {
  return list(value, 'questions').map((question) => ({
    questionId: text(question, 'question_id'),
    title: optionalText(question, 'title'),
    question: text(question, 'question'),
    multiple: flag(question, 'multiple'),
    choices: list(question, 'choices').map((choice) => ({
      label: text(choice, 'label'),
      description: optionalText(choice, 'description'),
    })),
  }));
}

function messagePhase(
  value: unknown,
): 'prompt' | 'intermediate' | 'end_turn' | 'synthetic' | 'recap' | null {
  const candidate = optionalText(value, 'phase');
  switch (candidate) {
    case null:
    case 'prompt':
    case 'intermediate':
    case 'end_turn':
    case 'synthetic':
    case 'recap':
      return candidate;
    default:
      throw new EntryValidationFailure('entry has an unknown message phase');
  }
}

type EntryWireContent = {
  readonly type: EntryContent['type'];
  readonly body: unknown;
};

function entryType(value: unknown, name: string): EntryContent['type'] {
  const candidate = text(value, name);
  switch (candidate) {
    case 'turn_started':
    case 'turn_finished':
    case 'message':
    case 'reasoning':
    case 'shell_started':
    case 'shell_output':
    case 'shell_backgrounded':
    case 'shell_finished':
    case 'file':
    case 'search':
    case 'web':
    case 'worktree':
    case 'skill_started':
    case 'skill_finished':
    case 'question_asked':
    case 'question_answered':
    case 'plan_proposed':
    case 'plan_resolved':
    case 'compaction_started':
    case 'compaction_finished':
    case 'assignment_started':
    case 'assignment_finished':
    case 'model_change':
    case 'effort_change':
      return candidate;
    default:
      throw new EntryValidationFailure('entry has an unknown type');
  }
}

function entryContent(wire: EntryWireContent): EntryContent {
  const body: unknown = wire.body;
  switch (wire.type) {
    case 'turn_started':
      return { type: wire.type, body: {} };
    case 'turn_finished':
      return {
        type: wire.type,
        body: { state: oneOf(body, 'state', ['finished', 'aborted']) },
      };
    case 'message': {
      const recipient = optionalText(body, 'recipient_actor_id');
      return {
        type: wire.type,
        body: {
          messageId: text(body, 'message_id'),
          role: oneOf(body, 'role', [
            'user',
            'assistant',
            'system',
            'peer',
            'parent',
          ]),
          phase: messagePhase(body),
          content: content(field(body, 'content')),
          recipientActorId: recipient === null ? null : actorId(recipient),
          replyTo: optionalText(body, 'reply_to'),
        },
      };
    }
    case 'reasoning':
      return {
        type: wire.type,
        body: {
          reasoningId: text(body, 'reasoning_id'),
          content: content(field(body, 'content')),
        },
      };
    case 'shell_started':
      return {
        type: wire.type,
        body: {
          shellId: text(body, 'shell_id'),
          command: content(field(body, 'command')),
          execution: oneOf(body, 'execution', [
            'foreground',
            'background',
            'monitor',
          ]),
        },
      };
    case 'shell_output':
      return {
        type: wire.type,
        body: {
          shellId: text(body, 'shell_id'),
          stream: oneOf(body, 'stream', ['output', 'error', 'status']),
          mode: oneOf(body, 'mode', ['append', 'replace']),
          content: content(field(body, 'content')),
        },
      };
    case 'shell_backgrounded':
      return { type: wire.type, body: { shellId: text(body, 'shell_id') } };
    case 'shell_finished':
      return {
        type: wire.type,
        body: {
          shellId: text(body, 'shell_id'),
          state: oneOf(body, 'state', ['succeeded', 'failed', 'cancelled']),
          exitCode: optionalCount(body, 'exit_code'),
          result: optionalContent(body, 'result'),
        },
      };
    case 'file':
      return {
        type: wire.type,
        body: {
          path: text(body, 'path'),
          action: oneOf(body, 'action', [
            'read',
            'created',
            'updated',
            'deleted',
            'renamed',
          ]),
          state: oneOf(body, 'state', ['succeeded', 'failed']),
          previousPath: optionalText(body, 'previous_path'),
          linesAdded: optionalCount(body, 'lines_added'),
          linesRemoved: optionalCount(body, 'lines_removed'),
          content: optionalContent(body, 'content'),
        },
      };
    case 'search':
      return {
        type: wire.type,
        body: {
          tool: text(body, 'tool'),
          query: content(field(body, 'query')),
          state: oneOf(body, 'state', ['succeeded', 'failed']),
          result: optionalContent(body, 'result'),
        },
      };
    case 'web':
      return {
        type: wire.type,
        body: {
          url: optionalText(body, 'url'),
          state: oneOf(body, 'state', ['succeeded', 'failed']),
          result: optionalContent(body, 'result'),
        },
      };
    case 'worktree':
      return {
        type: wire.type,
        body: {
          action: oneOf(body, 'action', ['entered', 'exited']),
          state: oneOf(body, 'state', ['succeeded', 'failed']),
          arguments: optionalContent(body, 'arguments'),
        },
      };
    case 'skill_started':
      return {
        type: wire.type,
        body: {
          skillId: text(body, 'skill_id'),
          name: text(body, 'name'),
          arguments: optionalContent(body, 'arguments'),
        },
      };
    case 'skill_finished':
      return {
        type: wire.type,
        body: {
          skillId: text(body, 'skill_id'),
          state: oneOf(body, 'state', ['succeeded', 'failed', 'cancelled']),
          result: optionalContent(body, 'result'),
        },
      };
    case 'question_asked':
      return {
        type: wire.type,
        body: {
          attentionId: text(body, 'attention_id'),
          questions: questions(body),
        },
      };
    case 'question_answered':
      return {
        type: wire.type,
        body: {
          attentionId: text(body, 'attention_id'),
          answers: list(body, 'answers').map((answer) => ({
            questionId: text(answer, 'question_id'),
            labels: stringList(answer, 'labels'),
          })),
          feedback: optionalText(body, 'feedback'),
        },
      };
    case 'plan_proposed':
      return {
        type: wire.type,
        body: {
          attentionId: text(body, 'attention_id'),
          plan: content(field(body, 'plan')),
        },
      };
    case 'plan_resolved':
      return {
        type: wire.type,
        body: {
          attentionId: text(body, 'attention_id'),
          state: oneOf(body, 'state', [
            'approved',
            'changes_requested',
            'rejected',
          ]),
          feedback: optionalText(body, 'feedback'),
          edited: flag(body, 'edited'),
        },
      };
    case 'compaction_started':
      return {
        type: wire.type,
        body: { beforeTokens: optionalCount(body, 'before_tokens') },
      };
    case 'compaction_finished':
      return {
        type: wire.type,
        body: {
          beforeTokens: optionalCount(body, 'before_tokens'),
          afterTokens: optionalCount(body, 'after_tokens'),
        },
      };
    case 'assignment_started':
      return {
        type: wire.type,
        body: {
          assignmentId: text(body, 'assignment_id'),
          assignedActorName: optionalText(body, 'assigned_actor_name'),
          prompt: optionalContent(body, 'prompt'),
        },
      };
    case 'assignment_finished':
      return {
        type: wire.type,
        body: {
          assignmentId: text(body, 'assignment_id'),
          state: oneOf(body, 'state', ['succeeded', 'failed', 'cancelled']),
          result: optionalContent(body, 'result'),
        },
      };
    case 'model_change':
      return {
        type: wire.type,
        body: {
          current: text(body, 'current'),
          previous: optionalText(body, 'previous'),
          automatic: flag(body, 'automatic'),
        },
      };
    case 'effort_change':
      return {
        type: wire.type,
        body: {
          current: text(body, 'current'),
          previous: optionalText(body, 'previous'),
        },
      };
  }
}

export function translateEntry(wire: Schemas['EntryResponse']): Entry {
  const parent = wire.parent_actor_id;
  return {
    entryId: entryId(wire.entry_id),
    cursor: wire.cursor,
    actorId: actorId(wire.actor_id),
    parentActorId: parent === null ? null : actorId(parent),
    turnId: wire.turn_id,
    occurredAt: wire.occurred_at,
    summary: wire.summary,
    ...entryContent(wire),
  };
}

export function decodeEntry(value: unknown): Entry {
  const parent = optionalText(value, 'parent_actor_id');
  return {
    entryId: entryId(text(value, 'entry_id')),
    cursor: finiteNumber(value, 'cursor'),
    actorId: actorId(text(value, 'actor_id')),
    parentActorId: parent === null ? null : actorId(parent),
    turnId: optionalText(value, 'turn_id'),
    occurredAt: finiteNumber(value, 'occurred_at'),
    summary: optionalText(value, 'summary'),
    ...entryContent({
      type: entryType(value, 'type'),
      body: field(value, 'body'),
    }),
  };
}

export function translateEntryPage(
  wire: Schemas['EntryPageResponse'],
): EntryPage {
  return {
    items: wire.items.map(translateEntry),
    oldestCursor: wire.oldest_cursor,
    hasMore: wire.has_more,
  };
}
