const RETRY_DELAY_MS = 4_000;

export class StreamRecovery {
  private timer: ReturnType<typeof setTimeout> | null = null;
  private destroyed = false;

  constructor(
    private readonly reconnect: () => void,
    private readonly delayMilliseconds = RETRY_DELAY_MS,
  ) {}

  disconnected(): void {
    if (this.destroyed || this.timer !== null) return;
    this.timer = setTimeout(() => {
      this.timer = null;
      this.reconnect();
    }, this.delayMilliseconds);
  }

  opened(): void {
    this.cancel();
  }

  destroy(): void {
    this.destroyed = true;
    this.cancel();
  }

  private cancel(): void {
    if (this.timer === null) return;
    clearTimeout(this.timer);
    this.timer = null;
  }
}
