/**
 * Follow one extension's record changes in one scope. The open stream holds
 * the scope on the host, so the scope's sources stay active while a view
 * watches it. EventSource connects again by itself after a drop.
 */
export function watchExtensionChanges(
  owner: string,
  scope: unknown,
  listener: () => void,
  signal: AbortSignal,
): () => void {
  const path = `/api/extensions/${encodeURIComponent(owner)}/changes`;
  const query = `scope=${encodeURIComponent(JSON.stringify(scope))}`;
  const source = new EventSource(`${path}?${query}`);
  source.addEventListener('changes', listener);
  source.addEventListener('reset', listener);
  const stop = (): void => {
    source.close();
    signal.removeEventListener('abort', stop);
  };
  signal.addEventListener('abort', stop);
  return stop;
}
