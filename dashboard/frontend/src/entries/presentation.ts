import type { ActorId } from '../app/domain-ids';
import type { ShellFold } from '../sessions/shell-fold';
import type { Content, Entry, Question } from './model';

type EntryGroup = 'messages' | 'files' | 'agents' | 'commands';
export type SummaryKind =
  | 'shell'
  | 'background'
  | 'monitor'
  | 'file_read'
  | 'file_write'
  | 'file_edit'
  | 'search'
  | 'network'
  | 'workspace'
  | 'skill'
  | 'message'
  | 'attention'
  | 'actor_assignment'
  | 'compaction'
  | 'state'
  | 'tool';
type ConversationKind =
  | 'message'
  | 'question'
  | 'plan'
  | 'answer'
  | 'plan_decision'
  | 'actor_message'
  | 'recap'
  | 'prompt'
  | 'system'
  | '';
type OutcomeState = 'succeeded' | 'failed' | 'cancelled' | null;

type EntryMetadata = {
  readonly key: string;
  readonly group: EntryGroup;
  readonly summaryKind: SummaryKind;
  readonly conversationKind: ConversationKind;
  readonly state: OutcomeState;
  readonly linesAdded: number;
  readonly linesRemoved: number;
  readonly final: boolean;
  readonly occurredAt: number;
  readonly turnId: string | null;
  readonly assignmentId: string | null;
  readonly messageId: string | null;
};

type ContentBody = {
  readonly kind: 'content';
  readonly content: Content;
};

type AnsiBody = {
  readonly kind: 'ansi';
  readonly text: string;
};

type CodeBody = {
  readonly kind: 'source' | 'diff';
  readonly text: string;
  readonly path: string;
};

type EmptyBody = { readonly kind: 'empty' };

type AnswersBody = {
  readonly kind: 'answers';
  readonly answers: readonly {
    readonly questionId: string;
    readonly question: string;
    readonly labels: readonly string[];
  }[];
  readonly feedback: string | null;
};

export type QuestionTextIndex = ReadonlyMap<
  string,
  ReadonlyMap<string, string>
>;

type PlanResolutionBody = {
  readonly kind: 'plan-resolution';
  readonly edited: boolean;
  readonly feedback: string | null;
};

type SkillBody = {
  readonly kind: 'skill';
  readonly arguments: Content | null;
  readonly output: Content | null;
};

export type PresentationBody =
  | ContentBody
  | AnsiBody
  | CodeBody
  | EmptyBody
  | AnswersBody
  | PlanResolutionBody
  | SkillBody;

type HiddenEntryPresentation = { readonly kind: 'hidden' };

type MessageEntryPresentation = EntryMetadata & {
  readonly kind: 'message';
  readonly className: string;
  readonly label: string;
  readonly body: PresentationBody;
  readonly questions: readonly Question[];
};

type FileEntryPresentation = EntryMetadata & {
  readonly kind: 'file';
  readonly action: 'read' | 'created' | 'updated' | 'deleted' | 'renamed';
  readonly path: string;
  readonly body: PresentationBody;
};

type BlockHeader =
  | {
      readonly kind: 'chip';
      readonly chipKind: 'tool';
      readonly label: string;
    }
  | { readonly kind: 'note'; readonly label: string }
  | {
      readonly kind: 'shell';
      readonly shellKind: 'cmd' | 'background' | 'monitor';
      readonly label: string;
    };

export type BlockEntryPresentation = EntryMetadata & {
  readonly kind: 'block';
  readonly header: BlockHeader;
  readonly summary: string;
  readonly body: PresentationBody;
  readonly note: boolean;
  readonly quiet: boolean;
  readonly finishedAt: number | null;
  readonly exitCode: number | null;
};

export type VisibleEntryPresentation =
  MessageEntryPresentation | FileEntryPresentation | BlockEntryPresentation;

export type EntryPresentation =
  HiddenEntryPresentation | VisibleEntryPresentation;

const HIDDEN: HiddenEntryPresentation = { kind: 'hidden' };
const EMPTY: EmptyBody = { kind: 'empty' };

const FILE_SUMMARY: Readonly<
  Record<FileEntryPresentation['action'], SummaryKind>
> = {
  read: 'file_read',
  created: 'file_write',
  updated: 'file_edit',
  deleted: 'file_edit',
  renamed: 'file_edit',
};

function actorName(entry: Entry, actors: ReadonlyMap<ActorId, string>): string {
  return actors.get(entry.actorId) ?? entry.actorId;
}

function contentBody(content: Content | null): PresentationBody {
  return content === null ? EMPTY : { kind: 'content', content };
}

function metadata(
  entry: Entry,
  group: EntryGroup,
  summaryKind: SummaryKind,
  conversationKind: ConversationKind = '',
  state: OutcomeState = null,
): EntryMetadata {
  const body = entry.body;
  return {
    key: entry.entryId,
    group,
    summaryKind,
    conversationKind,
    state,
    linesAdded: entry.type === 'file' ? (entry.body.linesAdded ?? 0) : 0,
    linesRemoved: entry.type === 'file' ? (entry.body.linesRemoved ?? 0) : 0,
    final: entry.type === 'message' && entry.body.phase === 'end_turn',
    occurredAt: entry.occurredAt,
    turnId: entry.turnId,
    assignmentId:
      'assignmentId' in body && typeof body.assignmentId === 'string'
        ? body.assignmentId
        : null,
    messageId:
      'messageId' in body && typeof body.messageId === 'string'
        ? body.messageId
        : null,
  };
}

function message(
  entry: Extract<Entry, { readonly type: 'message' }>,
  actors: ReadonlyMap<ActorId, string>,
): MessageEntryPresentation {
  const body = entry.body;
  const name = actorName(entry, actors);
  if (body.phase === 'recap')
    return {
      ...metadata(entry, 'messages', 'message', 'recap'),
      kind: 'message',
      className: 'recap',
      label: '↩ recap',
      body: contentBody(body.content),
      questions: [],
    };
  if (body.recipientActorId !== null)
    return {
      ...metadata(entry, 'messages', 'message', 'actor_message'),
      kind: 'message',
      className: 'message peer',
      label: `${name} → ${body.recipientActorId}`,
      body: contentBody(body.content),
      questions: [],
    };
  if (body.role === 'user' && body.phase === 'synthetic')
    return {
      ...metadata(entry, 'messages', 'message', 'system'),
      kind: 'message',
      className: 'prompt sys',
      label: '⚙ system',
      body: contentBody(body.content),
      questions: [],
    };
  if (body.role === 'user' && entry.parentActorId !== null)
    return {
      ...metadata(entry, 'messages', 'message', 'message'),
      kind: 'message',
      className: 'message',
      label: 'parent agent',
      body: contentBody(body.content),
      questions: [],
    };
  if (body.role === 'user')
    return {
      ...metadata(entry, 'messages', 'message', 'prompt'),
      kind: 'message',
      className: 'prompt',
      label: 'you',
      body: contentBody(body.content),
      questions: [],
    };
  if (body.role === 'parent')
    return {
      ...metadata(entry, 'messages', 'message', 'message'),
      kind: 'message',
      className: 'message',
      label: 'parent agent',
      body: contentBody(body.content),
      questions: [],
    };
  if (body.role !== 'assistant')
    return {
      ...metadata(entry, 'messages', 'message', 'system'),
      kind: 'message',
      className: 'prompt sys',
      label: '⚙ system',
      body: contentBody(body.content),
      questions: [],
    };
  return {
    ...metadata(entry, 'messages', 'message', 'message'),
    kind: 'message',
    className: 'message',
    label: name,
    body: contentBody(body.content),
    questions: [],
  };
}

function note(
  entry: Entry,
  label: string,
  body: PresentationBody,
  state: OutcomeState,
  summaryKind: SummaryKind,
  group: EntryGroup = 'commands',
): BlockEntryPresentation {
  return {
    ...metadata(entry, group, summaryKind, '', state),
    kind: 'block',
    header: { kind: 'note', label },
    summary: '',
    body,
    note: true,
    quiet: false,
    finishedAt: entry.occurredAt,
    exitCode: null,
  };
}

export function presentEntry(
  entry: Entry,
  actors: ReadonlyMap<ActorId, string>,
  questionText: QuestionTextIndex = new Map(),
  supportsReadableCompactionContext = true,
): EntryPresentation {
  switch (entry.type) {
    case 'turn_started':
    case 'turn_finished':
    case 'shell_started':
    case 'shell_output':
    case 'shell_backgrounded':
    case 'shell_finished':
    case 'skill_started':
    case 'skill_finished':
      return HIDDEN;
    case 'message':
      return message(entry, actors);
    case 'reasoning':
      return {
        ...metadata(entry, 'messages', 'message', 'message'),
        kind: 'message',
        className: 'thinking',
        label: actorName(entry, actors),
        body: contentBody(entry.body.content),
        questions: [],
      };
    case 'file':
      return {
        ...metadata(
          entry,
          'files',
          FILE_SUMMARY[entry.body.action],
          '',
          entry.body.state,
        ),
        kind: 'file',
        action: entry.body.action,
        path: entry.body.path,
        body:
          entry.body.content === null
            ? EMPTY
            : {
                kind:
                  entry.body.action === 'read' ||
                  entry.body.action === 'created'
                    ? 'source'
                    : 'diff',
                text: entry.body.content.text,
                path: entry.body.path,
              },
      };
    case 'search':
      return {
        ...metadata(entry, 'commands', 'search', '', entry.body.state),
        kind: 'block',
        header: { kind: 'chip', chipKind: 'tool', label: entry.body.tool },
        summary: entry.body.query.text,
        body:
          entry.body.result === null
            ? EMPTY
            : { kind: 'ansi', text: entry.body.result.text },
        note: false,
        quiet: true,
        finishedAt: entry.occurredAt,
        exitCode: null,
      };
    case 'web':
      return {
        ...metadata(entry, 'commands', 'network', '', entry.body.state),
        kind: 'block',
        header: { kind: 'chip', chipKind: 'tool', label: 'WebFetch' },
        summary: entry.body.url ?? '',
        body: contentBody(entry.body.result),
        note: false,
        quiet: true,
        finishedAt: entry.occurredAt,
        exitCode: null,
      };
    case 'browser':
      return {
        ...metadata(entry, 'commands', 'tool', '', entry.body.state),
        kind: 'block',
        header: { kind: 'chip', chipKind: 'tool', label: 'Browser' },
        summary: entry.body.action,
        body: contentBody(entry.body.result),
        note: false,
        quiet: true,
        finishedAt: entry.occurredAt,
        exitCode: null,
      };
    case 'worktree':
      return {
        ...metadata(entry, 'commands', 'workspace', '', entry.body.state),
        kind: 'block',
        header: {
          kind: 'chip',
          chipKind: 'tool',
          label:
            entry.body.action === 'entered' ? 'EnterWorktree' : 'ExitWorktree',
        },
        summary: entry.body.arguments?.text ?? '',
        body: EMPTY,
        note: false,
        quiet: true,
        finishedAt: entry.occurredAt,
        exitCode: null,
      };
    case 'question_asked':
      return {
        ...metadata(entry, 'messages', 'attention', 'question'),
        kind: 'message',
        className: 'question',
        label: `${actorName(entry, actors)} ▸ asks you`,
        body: EMPTY,
        questions: entry.body.questions,
      };
    case 'question_answered':
      return {
        ...metadata(entry, 'messages', 'attention', 'answer'),
        kind: 'message',
        className: 'answer',
        label: 'you ▸ answered',
        body: {
          kind: 'answers',
          answers: entry.body.answers.map((answer) => ({
            ...answer,
            question:
              questionText
                .get(entry.body.attentionId)
                ?.get(answer.questionId) ?? answer.questionId,
          })),
          feedback: entry.body.feedback,
        },
        questions: [],
      };
    case 'plan_proposed':
      return {
        ...metadata(entry, 'messages', 'attention', 'plan'),
        kind: 'message',
        className: 'plan',
        label: `${actorName(entry, actors)} ▸ proposes a plan`,
        body: contentBody(entry.body.plan),
        questions: [],
      };
    case 'plan_resolved': {
      const labels: Readonly<Record<typeof entry.body.state, string>> = {
        approved: 'you ▸ approved the plan',
        changes_requested: 'you ▸ asked for changes',
        rejected: 'you ▸ rejected the plan',
      };
      const classes: Readonly<Record<typeof entry.body.state, string>> = {
        approved: 'plandecision approved',
        changes_requested: 'plandecision changes',
        rejected: 'plandecision rejected',
      };
      return {
        ...metadata(entry, 'messages', 'attention', 'plan_decision'),
        kind: 'message',
        className: classes[entry.body.state],
        label: labels[entry.body.state],
        body: {
          kind: 'plan-resolution',
          edited: entry.body.edited,
          feedback: entry.body.feedback,
        },
        questions: [],
      };
    }
    case 'compaction_started':
      return note(entry, 'Compacting the context…', EMPTY, null, 'compaction');
    case 'compaction_finished': {
      const detail =
        entry.body.beforeTokens !== null && entry.body.afterTokens !== null
          ? ` · ${entry.body.beforeTokens.toLocaleString()} → ${entry.body.afterTokens.toLocaleString()} tokens`
          : '';
      return note(
        entry,
        `Context compacted${detail}`,
        contentBody(
          supportsReadableCompactionContext ? entry.body.context : null,
        ),
        'succeeded',
        'compaction',
      );
    }
    case 'assignment_started':
      return note(
        entry,
        `Agent ${entry.body.assignedActorName ?? 'agent'}${entry.summary === null ? '' : `: "${entry.summary}"`}`,
        contentBody(entry.body.prompt),
        null,
        'actor_assignment',
        'agents',
      );
    case 'assignment_finished':
      return note(
        entry,
        `Agent finished${entry.body.state === 'succeeded' ? '' : ` (${entry.body.state})`}`,
        contentBody(entry.body.result),
        entry.body.state,
        'actor_assignment',
        'agents',
      );
    case 'model_change':
      return note(
        entry,
        `Model ${entry.body.previous === null ? entry.body.current : `${entry.body.previous} → ${entry.body.current}`}${entry.body.automatic ? ' (chosen for you)' : ''}`,
        EMPTY,
        null,
        'state',
      );
    case 'effort_change':
      return note(
        entry,
        `Effort ${entry.body.previous === null ? entry.body.current : `${entry.body.previous} → ${entry.body.current}`}`,
        EMPTY,
        null,
        'state',
      );
  }
}

export function presentSkill(
  started: Extract<Entry, { readonly type: 'skill_started' }>,
  finished: Extract<Entry, { readonly type: 'skill_finished' }> | undefined,
  output: Content | null = finished?.body.result ?? null,
): BlockEntryPresentation {
  const state = finished?.body.state ?? null;
  const arguments_ = started.body.arguments;
  return {
    ...metadata(started, 'commands', 'skill', '', state),
    key: `skill:${started.body.skillId}`,
    kind: 'block',
    header: { kind: 'note', label: 'Skill' },
    summary: started.body.name,
    body:
      arguments_ === null && output === null
        ? EMPTY
        : { kind: 'skill', arguments: arguments_, output },
    note: true,
    quiet: true,
    finishedAt: finished?.occurredAt ?? null,
    exitCode: null,
  };
}

export function presentShell(fold: ShellFold): BlockEntryPresentation {
  const shellKind =
    fold.execution === 'monitor'
      ? 'monitor'
      : fold.backgrounded || fold.execution === 'background'
        ? 'background'
        : 'cmd';
  const state = fold.live ? null : fold.state;
  return {
    key: `shell:${fold.shellId}`,
    group: 'commands',
    summaryKind:
      shellKind === 'monitor'
        ? 'monitor'
        : shellKind === 'background'
          ? 'background'
          : 'shell',
    conversationKind: '',
    state,
    linesAdded: 0,
    linesRemoved: 0,
    final: false,
    occurredAt: fold.startedAt,
    turnId: null,
    assignmentId: null,
    messageId: null,
    kind: 'block',
    header: {
      kind: 'shell',
      shellKind,
      label: shellKind === 'cmd' ? '▶' : shellKind,
    },
    summary: fold.command,
    body:
      fold.statusOutput.length === 0 && fold.output.length === 0
        ? EMPTY
        : {
            kind: 'ansi',
            text: [fold.statusOutput, fold.output]
              .filter((value) => value.length > 0)
              .join('\n'),
          },
    note: false,
    quiet: true,
    finishedAt: fold.finishedAt,
    exitCode: fold.exitCode,
  };
}
