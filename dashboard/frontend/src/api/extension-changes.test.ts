import { afterEach, describe, expect, it, vi } from 'vitest';

import { watchExtensionChanges } from './extension-changes';
import { openedSource } from './fake-event-source.test-helper';

const SCOPE = { kind: 'repository', repository_id: 'repo-1' };

describe('extension change watch', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('calls the listener after each change and reset frame', () => {
    const listener = vi.fn();
    const source = openedSource(() => {
      watchExtensionChanges(
        'test.git',
        SCOPE,
        listener,
        new AbortController().signal,
      );
    });

    source.dispatchEvent(new MessageEvent('changes', { data: '{}' }));
    source.dispatchEvent(new MessageEvent('reset', { data: '{}' }));
    source.dispatchEvent(new MessageEvent('message', { data: '' }));

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
});
