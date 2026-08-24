import {
  recordClientFailure,
  recordOptimisticAction,
  type OptimisticAction,
} from '../../api/browser-telemetry';
import { HttpFailure } from '../../api/client';
import type { SessionId } from '../../app/domain-ids';

export type OptimisticPhase = 'reconciled' | 'dropped';

const STALE_AFTER_MS = 20_000;

export class OptimisticActionTracker {
  private readonly startedAt = performance.now();
  private timer: ReturnType<typeof setTimeout> | null;
  private active = true;

  constructor(
    private readonly sessionId: SessionId,
    private readonly action: OptimisticAction,
    private readonly characterCount: number | null = null,
  ) {
    this.beacon('shown', null);
    this.timer = setTimeout(() => {
      this.timer = null;
      if (this.active) this.beacon('stale', null);
    }, STALE_AFTER_MS);
  }

  settle(phase: OptimisticPhase, reason: string | null = null): void {
    if (!this.active) return;
    this.active = false;
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
    this.beacon(phase, reason);
  }

  cancel(): void {
    this.active = false;
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
  }

  private beacon(
    phase: 'shown' | 'reconciled' | 'dropped' | 'stale',
    reason: string | null,
  ): void {
    void recordOptimisticAction(
      this.sessionId,
      this.action,
      phase,
      Math.round(performance.now() - this.startedAt),
      this.characterCount,
      reason,
    ).catch(() => undefined);
  }
}

export function reportClientFailure(
  sessionId: SessionId,
  gesture: string,
  error: unknown,
  characterCount: number | null = null,
): void {
  const http = error instanceof HttpFailure;
  const detail = error instanceof Error ? error.message : String(error);
  void recordClientFailure(
    sessionId,
    gesture,
    http ? 'http' : 'transport',
    detail,
    http ? error.status : null,
    characterCount,
  ).catch(() => undefined);
}
