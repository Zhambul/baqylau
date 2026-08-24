import type { SessionId } from '../../app/domain-ids';
import { lastActiveAt } from '../../sessions/model';
import type { SessionSnapshot } from '../../sessions/model';

export function handleReadlineKey(event: KeyboardEvent): boolean {
  if (
    !event.ctrlKey ||
    event.altKey ||
    event.metaKey ||
    event.shiftKey ||
    !isTextControl(event.target)
  )
    return false;
  const control = event.target;
  const start = control.selectionStart ?? 0;
  const end = control.selectionEnd ?? start;
  const value = control.value;
  let cursor: number;
  if (event.code === 'KeyW') {
    let left = start;
    if (left === end) {
      while (left > 0 && /\s/.test(value[left - 1] ?? '')) left -= 1;
      while (left > 0 && !/\s/.test(value[left - 1] ?? '')) left -= 1;
    }
    control.value = `${value.slice(0, left)}${value.slice(end)}`;
    control.setSelectionRange(left, left);
    control.dispatchEvent(new Event('input', { bubbles: true }));
  } else if (event.code === 'KeyA') {
    cursor = start === 0 ? 0 : value.lastIndexOf('\n', start - 1) + 1;
    control.setSelectionRange(cursor, cursor);
  } else if (event.code === 'KeyE') {
    const newline = value.indexOf('\n', end);
    cursor = newline < 0 ? value.length : newline;
    control.setSelectionRange(cursor, cursor);
  } else return false;
  event.preventDefault();
  return true;
}

export function cycleLiveSession(
  sessions: readonly SessionSnapshot[],
  currentSessionId: SessionId | null,
  direction: 1 | -1,
): SessionId | null {
  const live = sessions
    .filter((snapshot) => snapshot.live)
    .sort(
      (left, right) =>
        orderKey(left) - orderKey(right) ||
        left.session.sessionId.localeCompare(right.session.sessionId),
    );
  if (live.length === 0) return null;
  const current = live.findIndex(
    (snapshot) => snapshot.session.sessionId === currentSessionId,
  );
  const next =
    current < 0
      ? direction > 0
        ? 0
        : live.length - 1
      : (current + direction + live.length) % live.length;
  return live[next]?.session.sessionId ?? null;
}

function isTextControl(
  target: EventTarget | null,
): target is HTMLInputElement | HTMLTextAreaElement {
  return (
    target instanceof HTMLTextAreaElement ||
    (target instanceof HTMLInputElement && target.type === 'text')
  );
}

function orderKey(snapshot: SessionSnapshot): number {
  return snapshot.session.startedAt ?? lastActiveAt(snapshot);
}
