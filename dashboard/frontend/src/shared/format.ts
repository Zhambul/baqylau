export function compactNumber(value: number): string {
  const safe = Number.isFinite(value) ? value : 0;
  if (safe >= 999_500) {
    return `${(safe / 1_000_000).toFixed(1).replace(/\.0$/, '')}M`;
  }
  if (safe >= 1_000) {
    return `${String(Math.round(safe / 1_000))}k`;
  }
  return String(safe);
}

export function dollars(value: number | null): string {
  if (value === null || !Number.isFinite(value)) {
    return '';
  }
  if (value === 0) {
    return '$0';
  }
  if (value < 0.005) {
    return '<$0.01';
  }
  if (value < 10) {
    return `$${value.toFixed(2)}`;
  }
  if (value < 1_000) {
    return `$${String(Math.round(value))}`;
  }
  return `$${(value / 1_000).toFixed(1)}k`;
}

export function timeAgo(timestamp: number, now = Date.now() / 1_000): string {
  if (timestamp <= 0) {
    return '';
  }
  const seconds = now - timestamp;
  if (seconds < 90) {
    return 'just now';
  }
  if (seconds < 3_600) {
    return `${String(Math.trunc(seconds / 60))}m ago`;
  }
  if (seconds < 86_400) {
    return `${String(Math.trunc(seconds / 3_600))}h ago`;
  }
  return `${String(Math.trunc(seconds / 86_400))}d ago`;
}

export function duration(seconds: number): string {
  if (seconds > 0 && seconds < 1) return '<1s';
  const total = Math.max(0, Math.round(seconds));
  if (total < 60) return `${String(total)}s`;
  if (total < 3_600)
    return `${String(Math.floor(total / 60))}m ${String(total % 60)}s`;
  return `${String(Math.floor(total / 3_600))}h ${String(Math.floor((total % 3_600) / 60))}m`;
}
