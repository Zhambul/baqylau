import { afterEach, describe, expect, it, vi } from 'vitest';

import { StreamRecovery } from './stream-recovery';

describe('stream recovery', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it('reconnects once after a sustained disconnect', () => {
    vi.useFakeTimers();
    const reconnect = vi.fn();
    const recovery = new StreamRecovery(reconnect, 25);

    recovery.disconnected();
    recovery.disconnected();
    vi.advanceTimersByTime(24);
    expect(reconnect).not.toHaveBeenCalled();

    vi.advanceTimersByTime(1);
    expect(reconnect).toHaveBeenCalledOnce();
  });

  it('keeps the native reconnect when the stream opens first', () => {
    vi.useFakeTimers();
    const reconnect = vi.fn();
    const recovery = new StreamRecovery(reconnect, 25);

    recovery.disconnected();
    recovery.opened();
    vi.runAllTimers();

    expect(reconnect).not.toHaveBeenCalled();
  });

  it('does not reconnect after its owner is destroyed', () => {
    vi.useFakeTimers();
    const reconnect = vi.fn();
    const recovery = new StreamRecovery(reconnect, 25);

    recovery.disconnected();
    recovery.destroy();
    recovery.disconnected();
    vi.runAllTimers();

    expect(reconnect).not.toHaveBeenCalled();
  });
});
