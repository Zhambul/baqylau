import { describe, expect, it } from 'vitest';

import { translateEntry } from '../api/translators/entries';
import type { components } from '../api/generated/schema';
import {
  foldShellEntries,
  jobFolds,
  monitorFolds,
  runningSlots,
} from './shell-fold';

type WireEntry = components['schemas']['EntryResponse'];

function envelope(
  cursor: number,
  type: WireEntry['type'],
  body: WireEntry['body'],
): WireEntry {
  return {
    entry_id: `entry-${String(cursor)}`,
    type,
    cursor,
    actor_id: 'actor-lead',
    parent_actor_id: null,
    turn_id: 'turn-one',
    occurred_at: 100 + cursor,
    summary: 'watch tests',
    body,
  };
}

describe('shell fold', () => {
  it('applies append and replace chunks and uses aggregate running ids', () => {
    const entries = [
      envelope(1, 'shell_started', {
        shell_id: 'shell-one',
        command: { text: 'make test', media_type: 'text/plain' },
        execution: 'monitor',
      }),
      envelope(2, 'shell_output', {
        shell_id: 'shell-one',
        stream: 'status',
        mode: 'append',
        content: { text: 'first\n', media_type: 'text/plain' },
      }),
      envelope(3, 'shell_output', {
        shell_id: 'shell-one',
        stream: 'status',
        mode: 'replace',
        content: { text: 'latest\n', media_type: 'text/plain' },
      }),
      envelope(4, 'shell_finished', {
        shell_id: 'shell-one',
        state: 'succeeded',
        exit_code: 0,
        result: null,
      }),
    ].map(translateEntry);

    const folds = foldShellEntries([...entries].reverse(), ['shell-one']);

    expect(monitorFolds(folds)).toHaveLength(1);
    expect(jobFolds(folds)).toHaveLength(0);
    expect(folds[0]).toMatchObject({
      statusOutput: 'latest\n',
      state: 'succeeded',
      live: true,
    });
    expect(runningSlots(folds)).toEqual([
      {
        key: 'shell-one',
        kind: 'monitor',
        glyph: '◉',
        label: 'monitor',
      },
    ]);
  });

  it('classifies foreground and background jobs and ignores orphan updates', () => {
    const entries = [
      envelope(1, 'shell_output', {
        shell_id: 'missing',
        stream: 'output',
        mode: 'append',
        content: { text: 'ignored', media_type: 'text/plain' },
      }),
      envelope(2, 'shell_backgrounded', { shell_id: 'missing' }),
      envelope(3, 'shell_finished', {
        shell_id: 'missing',
        state: 'failed',
        exit_code: 1,
        result: null,
      }),
      envelope(4, 'shell_started', {
        shell_id: 'foreground-job',
        command: { text: 'make test', media_type: 'text/plain' },
        execution: 'foreground',
      }),
      envelope(5, 'shell_output', {
        shell_id: 'foreground-job',
        stream: 'output',
        mode: 'append',
        content: { text: 'first', media_type: 'text/plain' },
      }),
      envelope(6, 'shell_output', {
        shell_id: 'foreground-job',
        stream: 'output',
        mode: 'replace',
        content: { text: 'latest', media_type: 'text/plain' },
      }),
      envelope(7, 'shell_backgrounded', { shell_id: 'foreground-job' }),
      envelope(8, 'shell_finished', {
        shell_id: 'foreground-job',
        state: 'succeeded',
        exit_code: 0,
        result: { text: 'complete', media_type: 'text/plain' },
      }),
      envelope(9, 'shell_started', {
        shell_id: 'background-live',
        command: { text: 'watch logs', media_type: 'text/plain' },
        execution: 'background',
      }),
      envelope(10, 'shell_started', {
        shell_id: 'foreground-live',
        command: { text: 'npm test', media_type: 'text/plain' },
        execution: 'foreground',
      }),
    ].map(translateEntry);

    const folds = foldShellEntries([...entries].reverse(), [
      'background-live',
      'foreground-live',
    ]);

    expect(folds.map((fold) => fold.shellId)).toEqual([
      'foreground-live',
      'background-live',
      'foreground-job',
    ]);
    expect(jobFolds(folds).map((fold) => fold.shellId)).toEqual([
      'background-live',
      'foreground-job',
    ]);
    expect(monitorFolds(folds)).toEqual([]);
    expect(folds[2]).toMatchObject({
      output: 'complete',
      backgrounded: true,
      finishedAt: 108,
    });
    expect(runningSlots(folds)).toEqual([
      {
        key: 'foreground-live',
        kind: 'fg',
        glyph: '⚙',
        label: 'fg',
      },
      {
        key: 'background-live',
        kind: 'bg',
        glyph: '◷',
        label: 'bg',
      },
    ]);
  });
});
