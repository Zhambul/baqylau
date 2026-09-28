import { afterEach, describe, expect, it, vi } from 'vitest';

import { sessionId } from '../app/domain-ids';
import {
  type FakeEventSource,
  openedSource,
} from './fake-event-source.test-helper';
import { SessionStream, type SessionStreamCallbacks } from './session-stream';

function callbacks(): SessionStreamCallbacks {
  return {
    opened: vi.fn(),
    disconnected: vi.fn(),
    delta: vi.fn(),
    application: vi.fn(),
    reset: vi.fn(),
    invalid: vi.fn(),
  };
}

function open(
  viewRevision: number | null,
  handlers: SessionStreamCallbacks,
  entryCursor: number | null = null,
): FakeEventSource {
  return openedSource(() => {
    new SessionStream(
      sessionId('session-one'),
      { cursor: 12, viewRevision, entryCursor },
      handlers,
    );
  });
}

describe('session stream', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('names the known view revision so that a switch during a drop resets', () => {
    expect(open(null, callbacks()).url).toBe(
      '/sessionData/session-one/stream?after_cursor=12',
    );
    expect(open(3, callbacks()).url).toBe(
      '/sessionData/session-one/stream?after_cursor=12&view_revision=3',
    );
  });

  it('names the highest entry row so that a late projected entry is sent', () => {
    expect(open(3, callbacks(), 40).url).toBe(
      '/sessionData/session-one/stream?after_cursor=12&view_revision=3&after_entry=40',
    );
  });

  it('closes on a reset and reports the new view revision', () => {
    const handlers = callbacks();
    const source = open(3, handlers);

    source.dispatchEvent(
      new MessageEvent('reset', { data: '{"view_revision":4}' }),
    );

    expect(source.close).toHaveBeenCalledOnce();
    expect(handlers.reset).toHaveBeenCalledWith(4);
    expect(handlers.invalid).not.toHaveBeenCalled();
  });

  it('reports a reset frame without a revision as invalid', () => {
    const handlers = callbacks();
    const source = open(3, handlers);

    source.dispatchEvent(new MessageEvent('reset', { data: '{}' }));

    expect(handlers.reset).not.toHaveBeenCalled();
    expect(handlers.invalid).toHaveBeenCalledOnce();
  });
});
