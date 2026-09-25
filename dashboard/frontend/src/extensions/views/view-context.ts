import type {
  DirectoryRequest,
  DirectorySnapshot,
  ExtensionClient,
  ExtensionScope,
  ExtensionViewSnapshot,
  FeedEntrySubject,
  ThemeValues,
} from '@baqylau/extension-api';
import type { ExtensionBundle } from '@baqylau/extension-api/host';

import type { ViewSettings, WebView } from '../../api/extension-views';
import {
  readExtensionSettings,
  saveExtensionSettings,
} from '../../api/extension-settings';
import { watchExtensionChanges } from '../../api/extension-changes';
import {
  readExtensionJob,
  submitExtensionCommand,
} from '../../api/extension-commands';
import { runExtensionQuery } from '../../api/extension-queries';
import { readExtensionRuntime } from '../../api/extensions';

/** Name one active view's package files for the SDK view host. */
export function viewBundle(view: WebView): ExtensionBundle {
  return {
    extensionId: view.extension_id,
    packageDigest: view.package_digest,
    moduleUrl: view.module_url,
    styleUrls: view.style_urls,
  };
}

/** Capture the host values that one mount receives; nothing mutable crosses. */
export function viewSnapshot(
  view: WebView,
  scope: ExtensionScope,
  runtimeRevision: string,
  settings: ViewSettings,
  theme: ThemeValues,
  subject?: FeedEntrySubject,
): ExtensionViewSnapshot {
  return {
    ...(subject === undefined ? {} : { subject }),
    extensionId: view.extension_id,
    viewId: view.view_id,
    scope,
    runtimeRevision,
    settingsRevision: settings.settingsRevision,
    settings: settings.settings,
    theme,
  };
}

/** Read the dashboard's theme tokens from the page, with neutral defaults. */
export function pageTheme(element: Element): ThemeValues {
  const style = getComputedStyle(element);
  const token = (name: string, fallback: string): string =>
    style.getPropertyValue(name).trim() || fallback;
  return {
    mode: 'dark',
    background: token('--bg', '#0a0e15'),
    foreground: token('--text', '#d0dcf0'),
    muted: token('--dim', '#6b7a93'),
    accent: token('--mid', '#a8c0e8'),
    fontFamily: style.fontFamily || 'system-ui',
    fontSize: style.fontSize || '14px',
  };
}

/**
 * Give a view the public directory read, and the settings, queries, record
 * changes, commands, and jobs of its own extension in its own scope, bounded
 * by its mount's signal.
 */
export function hostClient(
  snapshot: ExtensionViewSnapshot,
  signal: AbortSignal,
): ExtensionClient {
  const owner = snapshot.extensionId;
  const scope = snapshot.scope;
  return {
    listExtensions: async (
      request: DirectoryRequest,
    ): Promise<DirectorySnapshot> => {
      const runtime = await readExtensionRuntime(signal);
      const directory = runtime.directory;
      if (directory === null) {
        throw new Error('The extension directory is not available.');
      }
      const entries = request.active_only
        ? directory.entries.filter((entry) => entry.state === 'enabled')
        : directory.entries;
      return { ...directory, entries };
    },
    readSettings: async () => {
      const { settings } = await readExtensionSettings(owner, scope, signal);
      return {
        settingsRevision: settings.settings_revision,
        override: settings.override,
        effective: settings.effective,
      };
    },
    saveSettings: async (document, expectedSettingsRevision) => {
      const operation = await saveExtensionSettings(
        owner,
        scope,
        document,
        expectedSettingsRevision,
        signal,
      );
      return { operationId: operation.operation_id };
    },
    query: (queryId, argumentsJson, page) =>
      runExtensionQuery({ owner, queryId, scope }, argumentsJson, signal, page),
    watchChanges: (listener) =>
      watchExtensionChanges(owner, scope, listener, signal),
    runCommand: (commandId, requestKey, argumentsJson, expectedStateRevision) =>
      submitExtensionCommand(
        { owner, scope },
        { commandId, requestKey, argumentsJson, expectedStateRevision },
        signal,
      ),
    readJob: (jobId) => readExtensionJob({ owner, scope }, jobId, signal),
  };
}
