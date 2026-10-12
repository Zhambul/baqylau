import { afterEach, describe, expect, it, vi } from 'vitest';

import { watchExtensionChanges } from './extension-changes';
import { openedSource } from './fake-event-source.test-helper';

const SCOPE = { kind: 'repository', repository_id: 'repo-1' };
const OTHER_SCOPE = { kind: 'repository', repository_id: 'repo-2' };

function change(): MessageEvent {
  return new MessageEvent('changes', { data: '{}' });
}

describe('extension change watch', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('calls the listener after each change and reset frame', () => {
    const listener = vi.fn();
    const mount = new AbortController();
    const source = openedSource(() => {
      watchExtensionChanges('test.git', SCOPE, listener, mount.signal);
    });

    source.dispatchEvent(change());
    source.dispatchEvent(new MessageEvent('reset', { data: '{}' }));
    source.dispatchEvent(new MessageEvent('message', { data: '' }));
    mount.abort();

    expect(source.url).toBe(
      `/api/extensions/test.git/changes?scope=${encodeURIComponent(JSON.stringify(SCOPE))}`,
    );
    expect(listener).toHaveBeenCalledTimes(2);
  });

  it('closes the stream when stopped or when the view signal aborts', () => {
    let stop = (): void => undefined;
    const stopped = openedSource(() => {
      stop = watchExtensionChanges(
        'test.git',
        SCOPE,
        vi.fn(),
        new AbortController().signal,
      );
    });
    stop();
    const mount = new AbortController();
    const aborted = openedSource(() => {
      watchExtensionChanges('test.git', SCOPE, vi.fn(), mount.signal);
    });
    mount.abort();

    expect(stopped.close).toHaveBeenCalledOnce();
    expect(aborted.close).toHaveBeenCalledOnce();
  });

  it('shares one stream among the views of one extension and scope', () => {
    const first = vi.fn();
    const second = vi.fn();
    const firstMount = new AbortController();
    const secondMount = new AbortController();
    const source = openedSource(() => {
      watchExtensionChanges('test.adapters', SCOPE, first, firstMount.signal);
    });
    const shared = openedSource(() => {
      watchExtensionChanges('test.adapters', SCOPE, second, secondMount.signal);
    });

    source.dispatchEvent(change());
    firstMount.abort();
    source.dispatchEvent(change());
    const closedWhileWatched = source.close.mock.calls.length;
    secondMount.abort();

    expect(shared).toBe(source);
    expect([first.mock.calls.length, second.mock.calls.length]).toEqual([1, 2]);
    expect(closedWhileWatched).toBe(0);
    expect(source.close).toHaveBeenCalledOnce();
  });

  it('opens a stream of its own for another scope', () => {
    const mount = new AbortController();
    const one = openedSource(() => {
      watchExtensionChanges('test.adapters', SCOPE, vi.fn(), mount.signal);
    });
    const other = openedSource(() => {
      watchExtensionChanges(
        'test.adapters',
        OTHER_SCOPE,
        vi.fn(),
        mount.signal,
      );
    });
    mount.abort();

    expect(other).not.toBe(one);
    expect([
      one.close.mock.calls.length,
      other.close.mock.calls.length,
    ]).toEqual([1, 1]);
  });
});
