import { apiClient, execute } from './client';
import type { components } from './generated/schema';

type Schemas = components['schemas'];

export type ExtensionCatalog = Schemas['ExtensionCatalogResponse'];
export type ExtensionPackage = Schemas['ExtensionPackageResponse'];
export type ExtensionRuntime = Schemas['ExtensionRuntimeResponse'];
export type ExtensionOperation = Schemas['ExtensionOperationResponse'];
export type ExtensionPlan = Schemas['LifecyclePlanResponse'];
export type ExtensionPreviewRequest = Schemas['LifecyclePreviewRequest'];
export type ExtensionChangeRequest = Schemas['LifecycleChangeRequest'];
export type ExtensionAction = ExtensionPreviewRequest['action'];
export type ExtensionHealthReport = Schemas['ExtensionHealthResponse'];
export type ExtensionHealth = ExtensionHealthReport['extensions'][number];

export function readExtensionCatalog(
  signal: AbortSignal,
): Promise<ExtensionCatalog> {
  return execute(() => apiClient.GET('/api/extensions', { signal }));
}

export function readExtensionRuntime(
  signal: AbortSignal,
): Promise<ExtensionRuntime> {
  return execute(() => apiClient.GET('/api/extensions/state', { signal }));
}

export function readExtensionOperation(
  operationId: string,
  signal: AbortSignal,
): Promise<ExtensionOperation> {
  return execute(() =>
    apiClient.GET('/api/extensions/operations/{operation_id}', {
      params: { path: { operation_id: operationId } },
      signal,
    }),
  );
}

/** Each extension's consecutive worker failures; no row means no failures. */
export function readExtensionHealth(
  signal: AbortSignal,
): Promise<ExtensionHealthReport> {
  return execute(() => apiClient.GET('/api/extensions/health', { signal }));
}

const RECENT_OPERATIONS = 20;

/** The newest lifecycle operations of all extensions, newest first. */
export async function readRecentOperations(
  signal: AbortSignal,
): Promise<readonly ExtensionOperation[]> {
  const reply = await execute(() =>
    apiClient.GET('/api/extensions/operations', {
      params: { query: { limit: RECENT_OPERATIONS } },
      signal,
    }),
  );
  return reply.operations;
}

export function previewExtension(
  owner: string,
  body: ExtensionPreviewRequest,
  signal: AbortSignal,
): Promise<ExtensionPlan> {
  return execute(() =>
    apiClient.POST('/api/extensions/{extension_id}/lifecycle/preview', {
      params: { path: { extension_id: owner } },
      body,
      signal,
    }),
  );
}

export async function changeExtension(
  owner: string,
  body: ExtensionChangeRequest,
  signal: AbortSignal,
): Promise<ExtensionOperation> {
  const reply = await execute(() =>
    apiClient.POST('/api/extensions/{extension_id}/lifecycle', {
      params: { path: { extension_id: owner } },
      body,
      signal,
    }),
  );
  return reply.operation;
}

export function rescanExtensions(
  revision: number,
  signal: AbortSignal,
): Promise<ExtensionCatalog> {
  return execute(() =>
    apiClient.POST('/api/extensions/rescan', {
      body: { expected_revision: revision },
      signal,
    }),
  );
}
