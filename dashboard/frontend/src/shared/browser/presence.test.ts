import { afterEach, describe, expect, it, vi } from 'vitest';

import { deviceId, sessionId } from '../../app/domain-ids';
import { PresenceController } from './presence';

describe('browser presence', () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('derives its cadence from the server lifetime', async () => {
    vi.useFakeTimers();
    vi.spyOn(document, 'hasFocus').mockReturnValue(true);
    const write = vi.fn().mockResolvedValue(undefined);
    const controller = new PresenceController(
      deviceId('device'),
      () => sessionId('session'),
      write,
    );

    controller.start();
    controller.setLifetime(10);
    await vi.advanceTimersByTimeAsync(8_100);

    expect(write).toHaveBeenCalledTimes(3);
    expect(write).toHaveBeenLastCalledWith('device', 'session', false);
    controller.destroy();
  });

  it('reports away immediately when the window loses focus', () => {
    vi.spyOn(document, 'hasFocus').mockReturnValue(true);
    const write = vi.fn().mockResolvedValue(undefined);
    const controller = new PresenceController(
      deviceId('device'),
      () => null,
      write,
    );
    controller.start();

    window.dispatchEvent(new Event('blur'));

    expect(write).toHaveBeenLastCalledWith('device', null, true);
    controller.destroy();
  });
});
