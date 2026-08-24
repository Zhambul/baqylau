import { describe, expect, it } from 'vitest';

import { sessionId } from '../../app/domain-ids';
import { translateSessionSnapshot } from '../../api/translators/session-data';
import { wireSnapshot } from '../../test/session-fixture';
import { cycleLiveSession, handleReadlineKey } from './keyboard';

describe('page keyboard behavior', () => {
  it('deletes the word left of the caret and emits input', () => {
    const input = document.createElement('input');
    input.type = 'text';
    input.value = 'one two  ';
    input.setSelectionRange(9, 9);
    let changed = false;
    input.addEventListener('input', () => (changed = true));
    const event = new KeyboardEvent('keydown', {
      code: 'KeyW',
      ctrlKey: true,
      cancelable: true,
    });
    Object.defineProperty(event, 'target', { value: input });

    expect(handleReadlineKey(event)).toBe(true);
    expect(input.value).toBe('one ');
    expect(input.selectionStart).toBe(4);
    expect(changed).toBe(true);
  });

  it('moves to readline boundaries and rejects unrelated keys and controls', () => {
    const textarea = document.createElement('textarea');
    textarea.value = 'first\nsecond line\nthird';
    textarea.setSelectionRange(12, 12);

    const home = new KeyboardEvent('keydown', {
      code: 'KeyA',
      ctrlKey: true,
      cancelable: true,
    });
    Object.defineProperty(home, 'target', { value: textarea });
    expect(handleReadlineKey(home)).toBe(true);
    expect(textarea.selectionStart).toBe(6);

    textarea.setSelectionRange(8, 8);
    const end = new KeyboardEvent('keydown', {
      code: 'KeyE',
      ctrlKey: true,
      cancelable: true,
    });
    Object.defineProperty(end, 'target', { value: textarea });
    expect(handleReadlineKey(end)).toBe(true);
    expect(textarea.selectionStart).toBe(17);

    const shifted = new KeyboardEvent('keydown', {
      code: 'KeyA',
      ctrlKey: true,
      shiftKey: true,
    });
    Object.defineProperty(shifted, 'target', { value: textarea });
    expect(handleReadlineKey(shifted)).toBe(false);

    const password = document.createElement('input');
    password.type = 'password';
    const unsupported = new KeyboardEvent('keydown', {
      code: 'KeyK',
      ctrlKey: true,
    });
    Object.defineProperty(unsupported, 'target', { value: password });
    expect(handleReadlineKey(unsupported)).toBe(false);
  });

  it('cycles oldest-first and enters from either end', () => {
    const older = translateSessionSnapshot(wireSnapshot('older'));
    const newer = translateSessionSnapshot(wireSnapshot('newer'));
    const sessions = [
      { ...newer, session: { ...newer.session, startedAt: 20 } },
      { ...older, session: { ...older.session, startedAt: 10 } },
    ];

    expect(cycleLiveSession(sessions, null, 1)).toBe('older');
    expect(cycleLiveSession(sessions, null, -1)).toBe('newer');
    expect(cycleLiveSession(sessions, sessionId('newer'), 1)).toBe('older');
  });

  it('returns no target without live sessions and breaks order ties by id', () => {
    const beta = translateSessionSnapshot(wireSnapshot('beta'));
    const alpha = translateSessionSnapshot(wireSnapshot('alpha'));
    const stopped = {
      ...alpha,
      live: false,
      session: { ...alpha.session, startedAt: null },
    };
    expect(cycleLiveSession([stopped], null, 1)).toBeNull();

    const tied = [beta, alpha].map((snapshot) => ({
      ...snapshot,
      session: { ...snapshot.session, startedAt: 10 },
    }));
    expect(cycleLiveSession(tied, null, 1)).toBe('alpha');
    expect(cycleLiveSession(tied, sessionId('alpha'), -1)).toBe('beta');
  });
});
