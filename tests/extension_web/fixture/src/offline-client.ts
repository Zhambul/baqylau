import type { ExtensionClient } from '@baqylau/extension-api';

function refused(): Promise<never> {
  return Promise.reject(new Error('The fixture host has no daemon.'));
}

/** Give a view the extension list of a host with no daemon; every other call fails. */
export function offlineClient(runtimeRevision: string): ExtensionClient {
  return {
    listExtensions: () =>
      Promise.resolve({
        catalog_revision: 1,
        runtime_revision: runtimeRevision,
        entries: [],
      }),
    readSettings: refused,
    saveSettings: refused,
    query: refused,
    watchChanges: () => () => undefined,
    runCommand: refused,
    readJob: refused,
  };
}
