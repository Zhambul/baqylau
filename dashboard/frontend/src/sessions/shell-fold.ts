import type { ActorId } from '../app/domain-ids';
import type { Entry } from '../entries/model';

export type ShellFold = {
  readonly shellId: string;
  readonly actorId: ActorId;
  readonly command: string;
  readonly execution: 'foreground' | 'background' | 'monitor';
  readonly summary: string | null;
  readonly output: string;
  readonly statusOutput: string;
  readonly state: 'succeeded' | 'failed' | 'cancelled' | null;
  readonly exitCode: number | null;
  readonly backgrounded: boolean;
  readonly startedAt: number;
  readonly finishedAt: number | null;
  readonly live: boolean;
};

type RunningKind = 'fg' | 'bg' | 'monitor';

export type RunningSlot = {
  readonly key: string;
  readonly kind: RunningKind;
  readonly glyph: '⚙' | '◷' | '◉';
  readonly label: 'fg' | 'bg' | 'monitor';
};

type MutableShellFold = {
  -readonly [Key in keyof ShellFold]: ShellFold[Key];
};

function updateOutput(
  current: string,
  text: string,
  mode: 'append' | 'replace',
) {
  return mode === 'replace' ? text : current + text;
}

export function foldShellEntries(
  newestFirst: readonly Entry[],
  runningShellIds: readonly string[],
): readonly ShellFold[] {
  const running = new Set(runningShellIds);
  const folds = new Map<string, MutableShellFold>();
  for (const entry of [...newestFirst].reverse()) {
    switch (entry.type) {
      case 'shell_started':
        folds.set(entry.body.shellId, {
          shellId: entry.body.shellId,
          actorId: entry.actorId,
          command: entry.body.command.text,
          execution: entry.body.execution,
          summary: entry.summary,
          output: '',
          statusOutput: '',
          state: null,
          exitCode: null,
          backgrounded: false,
          startedAt: entry.occurredAt,
          finishedAt: null,
          live: false,
        });
        break;
      case 'shell_output': {
        const fold = folds.get(entry.body.shellId);
        if (fold === undefined) break;
        if (entry.body.stream === 'status') {
          fold.statusOutput = updateOutput(
            fold.statusOutput,
            entry.body.content.text,
            entry.body.mode,
          );
        } else {
          fold.output = updateOutput(
            fold.output,
            entry.body.content.text,
            entry.body.mode,
          );
        }
        break;
      }
      case 'shell_backgrounded': {
        const fold = folds.get(entry.body.shellId);
        if (fold !== undefined) fold.backgrounded = true;
        break;
      }
      case 'shell_finished': {
        const fold = folds.get(entry.body.shellId);
        if (fold === undefined) break;
        fold.state = entry.body.state;
        fold.exitCode = entry.body.exitCode;
        fold.finishedAt = entry.occurredAt;
        if (entry.body.result !== null && entry.body.result.text.length > 0)
          fold.output = entry.body.result.text;
        break;
      }
      default:
        break;
    }
  }
  for (const fold of folds.values()) fold.live = running.has(fold.shellId);
  return [...folds.values()].sort(
    (left, right) =>
      Number(right.live) - Number(left.live) ||
      right.startedAt - left.startedAt,
  );
}

export function monitorFolds(
  folds: readonly ShellFold[],
): readonly ShellFold[] {
  return folds.filter((fold) => fold.execution === 'monitor');
}

export function jobFolds(folds: readonly ShellFold[]): readonly ShellFold[] {
  return folds.filter(
    (fold) =>
      fold.execution === 'background' ||
      (fold.execution === 'foreground' && fold.backgrounded),
  );
}

export function runningSlots(
  folds: readonly ShellFold[],
): readonly RunningSlot[] {
  const slots: RunningSlot[] = [];
  for (const fold of folds) {
    if (!fold.live) continue;
    if (fold.execution === 'monitor') {
      slots.push({
        key: fold.shellId,
        kind: 'monitor',
        glyph: '◉',
        label: 'monitor',
      });
    } else if (fold.execution === 'background' || fold.backgrounded) {
      slots.push({
        key: fold.shellId,
        kind: 'bg',
        glyph: '◷',
        label: 'bg',
      });
    } else {
      slots.push({
        key: fold.shellId,
        kind: 'fg',
        glyph: '⚙',
        label: 'fg',
      });
    }
  }
  return slots;
}
