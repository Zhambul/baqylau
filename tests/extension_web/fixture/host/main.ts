import type { ExtensionViewSnapshot } from '@baqylau/extension-api';
import { ExtensionViewHost } from '@baqylau/extension-api/host';

function element(id: string): HTMLElement {
  const found = document.getElementById(id);
  if (!found) throw new Error(`Missing host element: ${id}`);
  return found;
}

let snapshot: ExtensionViewSnapshot = {
  extensionId: 'test.views',
  viewId: 'test.views.main',
  runtimeRevision: 'runtime-first',
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
const host = new ExtensionViewHost({
  target: element('view'),
  origin: location.origin,
  reportFailure: () => {
    element('failure').textContent = 'View failed.';
  },
  createClient: (current) => ({
    listExtensions: () =>
      Promise.resolve({
        catalog_revision: 1,
        runtime_revision: current.runtimeRevision,
        entries: [],
      }),
  }),
});
const revisions = new URLSearchParams(location.search);

async function load(revision: string): Promise<void> {
  const digest = revisions.get(revision);
  if (!digest) throw new Error('Missing package digest.');
  snapshot = {
    ...snapshot,
    runtimeRevision: `runtime-${revision}`,
    settingsRevision: 0,
  };
  const prefix = `/extensions/test.views/${digest}`;
  await host.show(
    {
      extensionId: snapshot.extensionId,
      packageDigest: digest,
      moduleUrl: `${prefix}/extension.js`,
      styleUrls: [`${prefix}/extension.css`],
    },
    snapshot,
  );
}

element('first').addEventListener('click', () => {
  void load('first');
});
element('second').addEventListener('click', () => {
  void load('second');
});
element('settings').addEventListener('click', () => {
  snapshot = { ...snapshot, settingsRevision: snapshot.settingsRevision + 1 };
  void host.update(snapshot);
});
element('disable').addEventListener('click', () => {
  void host.clear();
});
element('pulse').addEventListener('click', () => {
  window.dispatchEvent(new Event('fixture-pulse'));
});
