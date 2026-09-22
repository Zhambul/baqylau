import type { ExtensionViewContext, ExtensionViewSnapshot } from '../view.js';
import type { ViewHostOptions } from './types.js';

export function immutableSnapshot(
  source: ExtensionViewSnapshot,
): ExtensionViewSnapshot {
  const snapshot = structuredClone(source);
  Object.freeze(snapshot.scope);
  Object.freeze(snapshot.theme);
  if (snapshot.settings) {
    Object.freeze(snapshot.settings.schema_ref);
    Object.freeze(snapshot.settings);
  }
  return Object.freeze(snapshot);
}

export function viewContext(
  source: ExtensionViewSnapshot,
  signal: AbortSignal,
  factory: ViewHostOptions['createClient'],
): ExtensionViewContext {
  const snapshot = immutableSnapshot(source);
  const client = factory(snapshot, signal);
  return Object.freeze({
    ...snapshot,
    signal,
    api: Object.freeze({ listExtensions: client.listExtensions.bind(client) }),
  });
}
