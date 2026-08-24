import {
  deliverBrowserEvents,
  type BrowserEvent,
  type TelemetryFields,
} from '../../api/browser-telemetry';
import type { ClientId, DeviceId, SessionId } from '../../app/domain-ids';

const EVENT_LIMIT = 100;
const FLUSH_DELAY_MS = 500;
const RETRY_DELAY_MS = 4_000;

export class BrowserAudit {
  private events: BrowserEvent[] = [];
  private timer: ReturnType<typeof setTimeout> | null = null;
  private flushing = false;
  private readonly streamStates = new Map<string, boolean>();

  constructor(
    private readonly clientId: ClientId,
    private readonly deviceId: DeviceId,
    private readonly connection: () => TelemetryFields,
  ) {}

  record(
    sessionId: SessionId | null,
    name: string,
    details: TelemetryFields = {},
  ): void {
    if (this.flushing) return;
    try {
      this.events.push({
        timestamp: Date.now(),
        sessionId,
        name,
        details,
      });
      if (this.events.length > EVENT_LIMIT)
        this.events.splice(0, this.events.length - EVENT_LIMIT);
      this.schedule(FLUSH_DELAY_MS);
    } catch {
      // Telemetry must never break the user action that it describes.
    }
  }

  markStream(
    label: string,
    connected: boolean,
    sessionId: SessionId | null = null,
    details: TelemetryFields = {},
  ): void {
    if (this.streamStates.get(label) === connected) return;
    this.streamStates.set(label, connected);
    this.record(sessionId, connected ? 'sse.open' : 'sse.drop', {
      stream: label,
      ...details,
    });
  }

  async flush(): Promise<void> {
    if (this.flushing || this.events.length === 0) return;
    if (this.timer !== null) {
      clearTimeout(this.timer);
      this.timer = null;
    }
    this.flushing = true;
    const batch = this.events.splice(0, this.events.length);
    try {
      const result = await deliverBrowserEvents({
        clientId: this.clientId,
        deviceId: this.deviceId,
        connection: this.connection(),
        events: batch,
      });
      if (result === 'rejected') return;
    } catch {
      this.events = [...batch, ...this.events].slice(0, EVENT_LIMIT);
      this.schedule(RETRY_DELAY_MS);
    } finally {
      this.flushing = false;
      if (this.events.length > 0) this.schedule(FLUSH_DELAY_MS);
    }
  }

  destroy(): void {
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
  }

  private schedule(delay: number): void {
    if (this.timer !== null) return;
    this.timer = setTimeout(() => {
      this.timer = null;
      void this.flush();
    }, delay);
  }
}
