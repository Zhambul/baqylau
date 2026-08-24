import type { SessionId } from '../app/domain-ids';
import type { EntryPage } from '../entries/model';
import { apiClient, execute } from './client';
import { translateEntryPage } from './translators/entries';

export type EntryPageRequest = {
  readonly at?: number;
  readonly before?: number;
  readonly limit: number;
  readonly signal?: AbortSignal;
};

export async function readEntryPage(
  sessionId: SessionId,
  request: EntryPageRequest,
): Promise<EntryPage> {
  const wire = await execute(() =>
    apiClient.GET('/sessionData/{session_id}/entries', {
      params: {
        path: { session_id: sessionId },
        query: {
          ...(request.at === undefined ? {} : { at: request.at }),
          ...(request.before === undefined ? {} : { before: request.before }),
          limit: request.limit,
        },
      },
      ...(request.signal === undefined ? {} : { signal: request.signal }),
    }),
  );
  return translateEntryPage(wire);
}
