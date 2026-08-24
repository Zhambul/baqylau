type WakeStateListener = (wanted: boolean) => void;

export class WakeLockController {
  private sentinel: WakeLockSentinel | null = null;
  private wanted = false;

  constructor(private readonly changed: WakeStateListener) {}

  get available(): boolean {
    return 'wakeLock' in navigator;
  }

  start(): void {
    document.addEventListener('visibilitychange', this.visibilityChanged);
  }

  async toggle(): Promise<void> {
    this.wanted = !this.wanted;
    this.changed(this.wanted);
    if (this.wanted) await this.acquire();
    else if (this.sentinel !== null) {
      try {
        await this.sentinel.release();
      } catch {
        // Wake Lock is an optional browser enhancement.
      }
      this.sentinel = null;
    }
  }

  destroy(): void {
    document.removeEventListener('visibilitychange', this.visibilityChanged);
    this.wanted = false;
    this.changed(false);
    if (this.sentinel !== null)
      void this.sentinel.release().catch(() => undefined);
    this.sentinel = null;
  }

  private async acquire(): Promise<void> {
    if (!this.available || !this.wanted || this.sentinel !== null) return;
    try {
      this.sentinel = await navigator.wakeLock.request('screen');
      this.sentinel.addEventListener(
        'release',
        () => {
          this.sentinel = null;
        },
        { once: true },
      );
    } catch {
      // A hidden page or user policy can deny the optional lock.
    }
  }

  private visibilityChanged = (): void => {
    if (document.visibilityState === 'visible') void this.acquire();
  };
}
