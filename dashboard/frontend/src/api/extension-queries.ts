import type {
  QueryPageCursor,
  QueryResult,
  QuerySnapshot,
} from '@baqylau/extension-api';

import { apiClient, execute } from './client';
import type { components } from './generated/schema';

type Schemas = components['schemas'];
type QueryReply =
  | Schemas['ExtensionQueryReadyResponse']
  | Schemas['ExtensionQueryFailedResponse'];
type SnapshotReply = Schemas['QuerySnapshot'];
type PageReply = Schemas['QueryPageCursor'];

/** The host's default page size of a declared query. */
const QUERY_LIMIT = 100;

/** Name one declared query of one extension in one scope. */
export type QueryTarget = {
  readonly owner: string;
  readonly queryId: string;
  readonly scope: unknown;
};

function snapshot(reply: SnapshotReply): QuerySnapshot {
  return { state_revision: reply.state_revision, cursor: reply.cursor ?? null };
}

function pageCursor(reply: PageReply): QueryPageCursor {
  return { ...reply, snapshot: snapshot(reply.snapshot) };
}

/**
 * Give the reply the SDK's wire shape. The dashboard's generated types mark
 * fields with a default as optional; the SDK types write each default out.
 */
function queryResult(reply: QueryReply): QueryResult {
  if (reply.status === 'failed') {
    const { diagnostic } = reply;
    return {
      status: 'failed',
      binding: reply.binding,
      diagnostic: { ...diagnostic, input_id: diagnostic.input_id ?? null },
    };
  }
  return {
    status: 'ready',
    binding: reply.binding,
    document: reply.document,
    snapshot: snapshot(reply.snapshot),
    next_page: reply.next_page ? pageCursor(reply.next_page) : null,
    content: reply.content,
  };
}

/** Run one declared query; the host checks the arguments and the result. */
export async function runExtensionQuery(
  target: QueryTarget,
  argumentsJson: string,
  signal: AbortSignal,
  page?: QueryPageCursor,
): Promise<QueryResult> {
  const reply = await execute(() =>
    apiClient.POST('/api/extensions/{extension_id}/queries/{query_id}', {
      params: {
        path: { extension_id: target.owner, query_id: target.queryId },
      },
      body: {
        scope: JSON.stringify(target.scope),
        arguments: argumentsJson,
        page: page ?? null,
        limit: QUERY_LIMIT,
      },
      signal,
    }),
  );
  return queryResult(reply);
}
