import { apiClient, execute } from './client';
import type { components } from './generated/schema';

type Schemas = components['schemas'];

export type WebView = Schemas['WebViewResponse'];
export type WebViews = Schemas['WebViewsResponse'];
type EncodedDocument = Schemas['EncodedDocument'];
type ExtensionScope = Schemas['RelatedScopesResponse']['scopes'][number];

export type ViewSettings = {
  readonly settingsRevision: number;
  readonly settings: EncodedDocument | null;
};

export function readWebViews(signal: AbortSignal): Promise<WebViews> {
  return execute(() => apiClient.GET('/api/extension-web/views', { signal }));
}

/** Read the settings that the published runtime resolves for one view scope. */
export async function readViewSettings(
  owner: string,
  scope: string,
  signal: AbortSignal,
): Promise<ViewSettings> {
  const reply = await execute(() =>
    apiClient.GET('/api/extension-web/views/{extension_id}/settings', {
      params: { path: { extension_id: owner }, query: { scope } },
      signal,
    }),
  );
  return {
    settingsRevision: reply.settings_revision,
    settings: reply.settings,
  };
}

/** Read the scopes that one scope relates to, such as a session's workspace. */
export async function readRelatedScopes(
  scope: string,
  signal: AbortSignal,
): Promise<readonly ExtensionScope[]> {
  const reply = await execute(() =>
    apiClient.GET('/api/extension-web/related-scopes', {
      params: { query: { scope } },
      signal,
    }),
  );
  return reply.scopes;
}

export type RepositoryScope = NonNullable<
  Schemas['RepositoryScopeResponse']['scope']
>;

/** Ask the daemon for the repository of a directory; null when it has none. */
export async function readRepositoryScope(
  directory: string,
  signal: AbortSignal,
): Promise<RepositoryScope | null> {
  const reply = await execute(() =>
    apiClient.GET('/api/extension-web/repository-scope', {
      params: { query: { directory } },
      signal,
    }),
  );
  return reply.scope ?? null;
}
