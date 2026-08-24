import { reportPresence } from '../../api/application';
import type { DeviceId, SessionId } from '../../app/domain-ids';

const MINIMUM_BEAT_MS = 2_000;
const BEATS_PER_TTL = 2.5;

type PresenceWriter = (
  deviceId: DeviceId,
  sessionId: SessionId | null,
  away: boolean,
) => Promise<void>;

export class PresenceController {
  private timer: ReturnType<typeof setInterval> | null = null;
  private beatMilliseconds = 0;
  private started = false;

  constructor(
    private readonly deviceId: DeviceId,
    private readonly currentSession: () => SessionId | null,
    private readonly write: PresenceWriter = reportPresence,
  ) {}

  start(): void {
    if (this.started) return;
    this.started = true;
    window.addEventListener('focus', this.beat);
    window.addEventListener('blur', this.away);
    document.addEventListener('visibilitychange', this.visibilityChanged);
    this.beat();
  }

  setLifetime(seconds: number): void {
    const next = Math.max(
      MINIMUM_BEAT_MS,
      Math.round((seconds * 1_000) / BEATS_PER_TTL),
    );
    if (next === this.beatMilliseconds) return;
    if (this.timer !== null) clearInterval(this.timer);
    this.beatMilliseconds = next;
    this.timer = setInterval(this.beat, next);
  }

  beat = (): void => {
    if (document.visibilityState !== 'visible' || !document.hasFocus()) return;
    void this.write(this.deviceId, this.currentSession(), false).catch(
      () => undefined,
    );
  };

  destroy(): void {
    if (!this.started) return;
    this.started = false;
    window.removeEventListener('focus', this.beat);
    window.removeEventListener('blur', this.away);
    document.removeEventListener('visibilitychange', this.visibilityChanged);
    if (this.timer !== null) clearInterval(this.timer);
    this.timer = null;
    this.beatMilliseconds = 0;
  }

  private away = (): void => {
    void this.write(this.deviceId, this.currentSession(), true).catch(
      () => undefined,
    );
  };

  private visibilityChanged = (): void => {
    if (document.visibilityState === 'visible') this.beat();
    else this.away();
  };
}
