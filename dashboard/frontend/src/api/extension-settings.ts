import { apiClient, execute } from './client';
import type { components } from './generated/schema';
import { newRequestId } from '../shared/browser/identity';

type Schemas = components['schemas'];

export type ExtensionSettings = Schemas['ExtensionSettingsResponse'];
export type SettingsSnapshot = Schemas['SettingsSnapshot'];
export type SettingsDocument = Schemas['EncodedDocument'];
export type SettingsScope = SettingsSnapshot['scope'];
export type ExtensionSecrets = Schemas['ExtensionSecretsResponse'];
export type ExtensionOperation = Schemas['ExtensionOperationResponse'];

/** A newer settings revision was accepted after the caller read its copy. */
export class StaleSettingsError extends Error {
  constructor() {
    super('The settings changed after they were read. Refresh and try again.');
    this.name = 'StaleSettingsError';
  }
}

export function readExtensionSettings(
  owner: string,
  scope: SettingsScope,
  signal: AbortSignal,
): Promise<ExtensionSettings> {
  return execute(() =>
    apiClient.GET('/api/extensions/{extension_id}/settings', {
      params: {
        path: { extension_id: owner },
        query: { scope: JSON.stringify(scope) },
      },
      signal,
    }),
  );
}

/**
 * Replace one scope's override, or reset it with null. The standard form and
 * custom panels both save here. The read gives the current lifecycle and
 * catalog revisions; the expected settings revision makes the write optimistic.
 */
export async function saveExtensionSettings(
  owner: string,
  scope: SettingsScope,
  document: SettingsDocument | null,
  expectedSettingsRevision: number,
  signal: AbortSignal,
): Promise<ExtensionOperation> {
  const current = (await readExtensionSettings(owner, scope, signal)).settings;
  if (current.settings_revision !== expectedSettingsRevision) {
    throw new StaleSettingsError();
  }
  const reply = await execute(() =>
    apiClient.PUT('/api/extensions/{extension_id}/settings', {
      params: { path: { extension_id: owner } },
      body: {
        action: 'settings',
        request_id: newRequestId(),
        expected_revision: current.lifecycle_revision,
        expected_catalog_revision: current.catalog_revision,
        package_digest: current.extension_info.package_digest,
        expected_settings_revision: expectedSettingsRevision,
        scope,
        document,
      },
      signal,
    }),
  );
  return reply.operation;
}

export function readExtensionSecrets(
  owner: string,
  signal: AbortSignal,
): Promise<ExtensionSecrets> {
  return execute(() =>
    apiClient.GET('/api/extensions/{extension_id}/secrets', {
      params: { path: { extension_id: owner } },
      signal,
    }),
  );
}

export function storeExtensionSecret(
  owner: string,
  name: string,
  secret: string,
  signal: AbortSignal,
): Promise<ExtensionSecrets> {
  return execute(() =>
    apiClient.PUT('/api/extensions/{extension_id}/secrets/{name}', {
      params: { path: { extension_id: owner, name } },
      body: { secret },
      signal,
    }),
  );
}

export function clearExtensionSecret(
  owner: string,
  name: string,
  signal: AbortSignal,
): Promise<ExtensionSecrets> {
  return execute(() =>
    apiClient.DELETE('/api/extensions/{extension_id}/secrets/{name}', {
      params: { path: { extension_id: owner, name } },
      signal,
    }),
  );
}
