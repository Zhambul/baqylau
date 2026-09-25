import { afterEach, describe, expect, it, vi } from 'vitest';

import { openedSource } from './fake-event-source.test-helper';
import { GlobalStream, type GlobalStreamCallbacks } from './global-stream';

function callbacks(): GlobalStreamCallbacks {
  return {
    opened: vi.fn(),
    disconnected: vi.fn(),
    delta: vi.fn(),
    application: vi.fn(),
    ready: vi.fn(),
    reset: vi.fn(),
    invalid: vi.fn(),
  };
}

describe('global stream', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('names the view revision and closes on a reset', () => {
    const handlers = callbacks();
    const source = openedSource(() => {
      new GlobalStream({ cursor: 5, viewRevision: 2 }, handlers);
    });

    source.dispatchEvent(
      new MessageEvent('reset', { data: '{"view_revision":3}' }),
    );

    expect(source.url).toBe(
      '/sessionData/stream?after_cursor=5&view_revision=2',
    );
    expect(source.close).toHaveBeenCalledOnce();
    expect(handlers.reset).toHaveBeenCalledWith(3);
  });
});
