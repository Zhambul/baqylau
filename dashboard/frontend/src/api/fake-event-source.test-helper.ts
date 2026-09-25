import { vi } from 'vitest';

const sources: FakeEventSource[] = [];

/** Stand in for the browser EventSource; tests dispatch server events on it. */
export class FakeEventSource extends EventTarget {
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  readonly close = vi.fn();

  constructor(readonly url: string) {
    super();
    sources.push(this);
  }
}

/** Replace the global EventSource, run the opening code, and return its source. */
export function openedSource(open: () => void): FakeEventSource {
  vi.stubGlobal('EventSource', FakeEventSource);
  open();
  const source = sources.at(-1);
  if (source === undefined) throw new Error('The code opened no source.');
  return source;
}
