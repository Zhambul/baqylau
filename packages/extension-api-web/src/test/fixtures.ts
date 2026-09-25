import { vi } from 'vitest';

import type {
  ExtensionViewSnapshot,
  ExtensionWebModule,
  MountedExtensionView,
} from '../index.js';
import { ExtensionViewHost } from '../host/index.js';
import type { ExtensionBundle } from '../host/index.js';

export const ORIGIN = 'http://localhost:8794';
export const DIGEST = 'a'.repeat(64);
export const BUNDLE: ExtensionBundle = {
  extensionId: 'test.views',
  packageDigest: DIGEST,
  moduleUrl: `${ORIGIN}/extensions/test.views/${DIGEST}/extension.js`,
  styleUrls: [],
};
export const SNAPSHOT: ExtensionViewSnapshot = {
  extensionId: BUNDLE.extensionId,
  viewId: 'test.views.main',
  runtimeRevision: 'runtime-1',
  settingsRevision: 0,
  settings: null,
  scope: { kind: 'workspace', workspace_id: 'workspace-1' },
  theme: {
    mode: 'dark',
    background: '#101010',
    foreground: '#eeeeee',
    muted: '#999999',
    accent: '#00aaff',
    fontFamily: 'monospace',
    fontSize: '14px',
  },
};

export function mountedView() {
  return {
    update: vi.fn<MountedExtensionView['update']>(),
    dispose: vi.fn<MountedExtensionView['dispose']>(),
  };
}

export function fixture(module: ExtensionWebModule) {
  const target = document.createElement('div');
  document.body.append(target);
  const errors = vi.fn<(error: unknown) => void>();
  const signals: AbortSignal[] = [];
  const importer = vi
    .fn<(url: string) => Promise<unknown>>()
    .mockResolvedValue(module);
  const host = new ExtensionViewHost({
    target,
    origin: ORIGIN,
    reportFailure: errors,
    importModule: importer,
    createClient: (snapshot, signal) => {
      signals.push(signal);
      return {
        listExtensions: () =>
          Promise.resolve({
            catalog_revision: 1,
            runtime_revision: snapshot.runtimeRevision,
            entries: [],
          }),
        readSettings: () => Promise.reject(new Error('No settings.')),
        saveSettings: () => Promise.reject(new Error('No settings.')),
        query: () => Promise.reject(new Error('No queries.')),
        watchChanges: () => () => undefined,
        runCommand: () => Promise.reject(new Error('No commands.')),
        readJob: () => Promise.reject(new Error('No jobs.')),
      };
    },
  });
  return { host, target, errors, signals, importer };
}

export function deferred<Result>() {
  let resolve: (result: Result) => void = () => {
    throw new Error('Deferred result is not initialized.');
  };
  const promise = new Promise<Result>((complete) => {
    resolve = complete;
  });
  return { promise, resolve };
}
