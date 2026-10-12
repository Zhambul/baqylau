/**
 * One change stream for each extension and scope, which every view of that
 * extension and scope shares. A page can mount one view for each feed row, and
 * a stream for each view took one connection each and woke every view once for
 * each stream.
 */
type SharedStream = {
  readonly source: EventSource;
  readonly listeners: Set<() => void>;
};

const streams = new Map<string, SharedStream>();

function notify(stream: SharedStream): void {
  for (const listener of [...stream.listeners]) listener();
}

function opened(url: string): SharedStream {
  const found = streams.get(url);
  if (found !== undefined) return found;
  const stream: SharedStream = {
    source: new EventSource(url),
    listeners: new Set(),
  };
  stream.source.addEventListener('changes', () => {
    notify(stream);
  });
  stream.source.addEventListener('reset', () => {
    notify(stream);
  });
  streams.set(url, stream);
  return stream;
}

/**
 * Follow one extension's record changes in one scope. The open stream holds
 * the scope on the host, so the scope's sources stay active while a view
 * watches it. EventSource connects again by itself after a drop. The stream
 * closes when the last view that watches it stops.
 */
export function watchExtensionChanges(
  owner: string,
  scope: unknown,
  listener: () => void,
  signal: AbortSignal,
): () => void {
  const path = `/api/extensions/${encodeURIComponent(owner)}/changes`;
  const url = `${path}?scope=${encodeURIComponent(JSON.stringify(scope))}`;
  const stream = opened(url);
  // A watch of its own, so that one view that watches twice stops each watch alone.
  const watch = (): void => {
    listener();
  };
  stream.listeners.add(watch);
  const stop = (): void => {
    signal.removeEventListener('abort', stop);
    if (!stream.listeners.delete(watch) || stream.listeners.size > 0) return;
    stream.source.close();
    streams.delete(url);
  };
  signal.addEventListener('abort', stop);
  return stop;
}
