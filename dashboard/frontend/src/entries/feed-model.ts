import type { ActorId } from '../app/domain-ids';
import type { ViewMode } from '../application/session-model';
import type { ShellFold } from '../sessions/shell-fold';
import type { Content, Entry } from './model';
import {
  presentEntry,
  presentShell,
  presentSkill,
  type QuestionTextIndex,
  type SummaryKind,
  type VisibleEntryPresentation,
} from './presentation';

const CLAUDE_SKILL_PREFIX = 'Base directory for this skill: ';
const SKILL_ARGUMENTS_MARKER = '\nARGUMENTS:';

export type FeedItem = VisibleEntryPresentation;

type SummaryFragment = {
  readonly verb: string;
  readonly count: number;
  readonly singular: string;
  readonly plural: string;
};

export type RunSummary = {
  readonly key: string;
  readonly open: boolean;
  readonly running: boolean;
  readonly bad: boolean;
  readonly anchor: number;
  readonly fragments: readonly SummaryFragment[];
  readonly linesAdded: number;
  readonly linesRemoved: number;
};

export type DensityUnit =
  | { readonly kind: 'summary'; readonly summary: RunSummary }
  | {
      readonly kind: 'item';
      readonly item: FeedItem;
      readonly extraClass: string;
      readonly defaultOpen: boolean;
    };

type Disposition = 'show' | 'hide' | 'dim' | 'fold';

const DEFAULT_FOLD = new Set<SummaryKind>(['shell', 'file_read', 'monitor']);
const FOCUS_FOLD = new Set<SummaryKind>([
  'shell',
  'file_read',
  'background',
  'monitor',
  'file_edit',
  'file_write',
  'actor_assignment',
  'skill',
  'tool',
  'search',
  'network',
  'workspace',
]);

const COUNTER: Readonly<Partial<Record<SummaryKind, string>>> = {
  file_edit: 'file_change',
  file_write: 'file_change',
  file_read: 'file_read',
  actor_assignment: 'actor_assignment',
  skill: 'skill',
  tool: 'tool',
  search: 'tool',
  network: 'tool',
  workspace: 'tool',
  shell: 'shell',
  background: 'background',
  monitor: 'monitor',
};

const FRAGMENTS: readonly {
  readonly key: string;
  readonly active: string;
  readonly done: string;
  readonly singular: string;
  readonly plural: string;
}[] = [
  {
    key: 'file_change',
    active: 'editing',
    done: 'edited',
    singular: 'file',
    plural: 'files',
  },
  {
    key: 'file_read',
    active: 'reading',
    done: 'read',
    singular: 'file',
    plural: 'files',
  },
  {
    key: 'actor_assignment',
    active: 'running',
    done: 'ran',
    singular: 'agent',
    plural: 'agents',
  },
  {
    key: 'skill',
    active: 'using',
    done: 'used',
    singular: 'skill',
    plural: 'skills',
  },
  {
    key: 'tool',
    active: 'using',
    done: 'used',
    singular: 'tool',
    plural: 'tools',
  },
  {
    key: 'shell',
    active: 'running',
    done: 'ran',
    singular: 'shell command',
    plural: 'shell commands',
  },
  {
    key: 'background',
    active: 'running',
    done: 'ran',
    singular: 'background job',
    plural: 'background jobs',
  },
  {
    key: 'monitor',
    active: 'watching',
    done: 'watched',
    singular: 'monitor',
    plural: 'monitors',
  },
];

function suppressReplacedPrompts(entries: readonly Entry[]): readonly Entry[] {
  const replies = new Set<string>();
  return entries.filter((entry) => {
    if (
      entry.type !== 'message' ||
      entry.body.role !== 'user' ||
      entry.body.phase === 'synthetic' ||
      entry.body.replyTo === null
    )
      return true;
    if (replies.has(entry.body.replyTo)) return false;
    replies.add(entry.body.replyTo);
    return true;
  });
}

function suppressDuplicateCompactionFinishes(
  entries: readonly Entry[],
): readonly Entry[] {
  // Entries are newest first. Keep the newest finish, which is the hook row
  // with token counts, and drop older rollout boundaries until the matching
  // start. This also repairs sessions recorded before rollout boundaries were
  // classified as hook plumbing.
  const finishedActors = new Set<ActorId>();
  return entries.filter((entry) => {
    if (entry.type === 'compaction_started') {
      finishedActors.delete(entry.actorId);
      return true;
    }
    if (entry.type !== 'compaction_finished') return true;
    if (finishedActors.has(entry.actorId)) return false;
    finishedActors.add(entry.actorId);
    return true;
  });
}

function questionTextIndex(entries: readonly Entry[]): QuestionTextIndex {
  const index = new Map<string, Map<string, string>>();
  for (const entry of entries) {
    if (entry.type !== 'question_asked') continue;
    let questions = index.get(entry.body.attentionId);
    if (questions === undefined) {
      questions = new Map();
      index.set(entry.body.attentionId, questions);
    }
    for (const question of entry.body.questions)
      questions.set(question.questionId, question.question);
  }
  return index;
}

function legacyClaudeSkillOutput(
  entry: Entry,
): { readonly name: string; readonly output: Content } | null {
  if (
    entry.type !== 'message' ||
    entry.body.role !== 'system' ||
    entry.body.phase !== 'synthetic'
  )
    return null;
  const text = entry.body.content.text;
  const firstLineEnd = text.indexOf('\n');
  const firstLine = text.slice(
    0,
    firstLineEnd < 0 ? text.length : firstLineEnd,
  );
  if (!firstLine.startsWith(CLAUDE_SKILL_PREFIX)) return null;
  const directory = firstLine
    .slice(CLAUDE_SKILL_PREFIX.length)
    .trim()
    .replace(/\/$/, '');
  const marker = '/.claude/skills/';
  const markerAt = directory.lastIndexOf(marker);
  if (markerAt < 0) return null;
  const name = directory.slice(markerAt + marker.length);
  if (name.length === 0 || name.includes('/')) return null;
  const argumentsAt = text.lastIndexOf(SKILL_ARGUMENTS_MARKER);
  return {
    name,
    output: {
      ...entry.body.content,
      text: (argumentsAt < 0 ? text : text.slice(0, argumentsAt)).trimEnd(),
    },
  };
}

function legacySkillOutputs(entries: readonly Entry[]): {
  readonly bySkillId: ReadonlyMap<string, Content>;
  readonly messageIds: ReadonlySet<string>;
} {
  const bySkillId = new Map<string, Content>();
  const messageIds = new Set<string>();
  const claimedSkills = new Set<string>();
  entries.forEach((entry, index) => {
    const loaded = legacyClaudeSkillOutput(entry);
    if (loaded === null) return;
    const started = entries
      .slice(index + 1)
      .find(
        (candidate) =>
          candidate.type === 'skill_started' &&
          candidate.actorId === entry.actorId &&
          candidate.body.name === loaded.name &&
          !claimedSkills.has(candidate.body.skillId) &&
          (entry.turnId === null ||
            candidate.turnId === null ||
            candidate.turnId === entry.turnId),
      );
    if (started?.type !== 'skill_started') return;
    claimedSkills.add(started.body.skillId);
    bySkillId.set(started.body.skillId, loaded.output);
    messageIds.add(entry.entryId);
  });
  return { bySkillId, messageIds };
}

export function buildFeedItems(
  entries: readonly Entry[],
  actors: ReadonlyMap<ActorId, string>,
  shells: readonly ShellFold[],
): readonly FeedItem[] {
  const shellById = new Map(shells.map((shell) => [shell.shellId, shell]));
  const skillFinishedById = new Map(
    entries.flatMap((entry) =>
      entry.type === 'skill_finished'
        ? ([[entry.body.skillId, entry]] as const)
        : [],
    ),
  );
  const legacySkills = legacySkillOutputs(entries);
  const questions = questionTextIndex(entries);
  const items: FeedItem[] = [];
  for (const entry of suppressDuplicateCompactionFinishes(
    suppressReplacedPrompts(entries),
  )) {
    if (legacySkills.messageIds.has(entry.entryId)) continue;
    if (entry.type === 'shell_started') {
      const fold = shellById.get(entry.body.shellId);
      if (fold !== undefined) items.push(presentShell(fold));
      continue;
    }
    if (entry.type === 'skill_finished') continue;
    if (entry.type === 'skill_started') {
      items.push(
        presentSkill(
          entry,
          skillFinishedById.get(entry.body.skillId),
          legacySkills.bySkillId.get(entry.body.skillId) ??
            skillFinishedById.get(entry.body.skillId)?.body.result ??
            null,
        ),
      );
      continue;
    }
    const presentation = presentEntry(entry, actors, questions);
    if (presentation.kind !== 'hidden') items.push(presentation);
  }
  return items;
}

function dispositions(
  items: readonly FeedItem[],
  mode: ViewMode,
  busy: boolean,
): readonly Disposition[] {
  if (mode === 'verbose') return items.map(() => 'show');
  const folded = mode === 'focus' ? FOCUS_FOLD : DEFAULT_FOLD;
  let sawReply = false;
  let inNewestTurn = true;
  return items.map((item) => {
    if (item.group === 'messages') {
      if (item.conversationKind === 'prompt') {
        sawReply = false;
        inNewestTurn = false;
        return 'show';
      }
      if (item.conversationKind === 'system') return 'hide';
      if (mode === 'focus' && item.conversationKind === 'message') {
        const newest = !sawReply;
        sawReply = true;
        if (!newest) return 'hide';
        return busy && inNewestTurn ? 'dim' : 'show';
      }
      return 'show';
    }
    return folded.has(item.summaryKind) ? 'fold' : 'show';
  });
}

function runFragments(
  members: readonly FeedItem[],
  running: boolean,
): readonly SummaryFragment[] {
  const counts = new Map<string, number>();
  const assignments = new Set<string>();
  for (const member of members) {
    const counter = COUNTER[member.summaryKind];
    if (counter === undefined) continue;
    if (counter === 'actor_assignment' && member.assignmentId !== null) {
      assignments.add(member.assignmentId);
      continue;
    }
    counts.set(counter, (counts.get(counter) ?? 0) + 1);
  }
  if (assignments.size > 0) counts.set('actor_assignment', assignments.size);
  const fragments: SummaryFragment[] = [];
  for (const vocabulary of FRAGMENTS) {
    const count = counts.get(vocabulary.key) ?? 0;
    if (count === 0) continue;
    let verb = running ? vocabulary.active : vocabulary.done;
    if (fragments.length === 0)
      verb = `${verb.slice(0, 1).toUpperCase()}${verb.slice(1)}`;
    fragments.push({
      verb,
      count,
      singular: vocabulary.singular,
      plural: vocabulary.plural,
    });
  }
  return fragments;
}

function isRunDisposition(disposition: Disposition): boolean {
  return disposition !== 'show';
}

export function planDensity(
  items: readonly FeedItem[],
  mode: ViewMode,
  busy: boolean,
  openRuns: ReadonlySet<string>,
): readonly DensityUnit[] {
  const disposition = dispositions(items, mode, busy);
  if (mode === 'verbose')
    return items.map((item) => ({
      kind: 'item',
      item,
      extraClass: '',
      defaultOpen: true,
    }));

  const summaryAt = new Map<number, RunSummary>();
  const openMember = new Set<number>();
  const lastOpenMember = new Set<number>();
  let index = 0;
  while (index < items.length) {
    if (!isRunDisposition(disposition[index] ?? 'show')) {
      index += 1;
      continue;
    }
    const start = index;
    let cursor = index;
    let lastFold = -1;
    while (
      cursor < items.length &&
      isRunDisposition(disposition[cursor] ?? 'show')
    ) {
      if (disposition[cursor] === 'fold') lastFold = cursor;
      cursor += 1;
    }
    index = cursor;
    if (lastFold < start) continue;
    const memberIndexes: number[] = [];
    for (let position = start; position <= lastFold; position += 1)
      if (disposition[position] === 'fold') memberIndexes.push(position);
    const members = memberIndexes.flatMap((position) => {
      const item = items[position];
      return item === undefined ? [] : [item];
    });
    const oldest = members.at(-1);
    if (oldest === undefined) continue;
    const key = oldest.key;
    const open = openRuns.has(key);
    const running =
      start === 0 && busy && members.some((member) => member.state === null);
    const summary: RunSummary = {
      key,
      open,
      running,
      bad: members.some(
        (member) => member.state === 'failed' || member.state === 'cancelled',
      ),
      anchor: running
        ? Math.min(...members.map((member) => member.occurredAt))
        : 0,
      fragments: runFragments(members, running),
      linesAdded: members.reduce(
        (total, member) => total + member.linesAdded,
        0,
      ),
      linesRemoved: members.reduce(
        (total, member) => total + member.linesRemoved,
        0,
      ),
    };
    summaryAt.set(start, summary);
    if (open) {
      const shown: number[] = [];
      for (let position = start; position <= lastFold; position += 1) {
        if (disposition[position] === 'hide') continue;
        openMember.add(position);
        shown.push(position);
      }
      const last = shown.at(-1);
      if (last !== undefined) lastOpenMember.add(last);
    }
  }

  const units: DensityUnit[] = [];
  items.forEach((item, position) => {
    const summary = summaryAt.get(position);
    if (summary !== undefined) units.push({ kind: 'summary', summary });
    const current = disposition[position] ?? 'show';
    const visible =
      current === 'show' ||
      current === 'dim' ||
      (current === 'fold' && openMember.has(position));
    if (!visible) return;
    const classes = [
      current === 'dim' ? 'vdim' : '',
      openMember.has(position) ? 'vrun' : '',
      lastOpenMember.has(position) ? 'vrun-last' : '',
    ].filter((value) => value.length > 0);
    units.push({
      kind: 'item',
      item,
      extraClass: classes.join(' '),
      defaultOpen: false,
    });
  });
  return units;
}

export function visibleDensityCount(units: readonly DensityUnit[]): number {
  return units.length;
}
