import type { SessionId } from '../app/domain-ids';
import type { SessionApplication } from '../application/session-model';
import { apiClient, execute } from './client';
import { translateSessionApplication } from './translators/session-application';

export async function readSessionApplication(
  sessionId: SessionId,
  signal?: AbortSignal,
): Promise<SessionApplication> {
  const wire = await execute(() =>
    apiClient.GET('/api/sessions/{session_id}/application', {
      params: { path: { session_id: sessionId } },
      ...(signal === undefined ? {} : { signal }),
    }),
  );
  return translateSessionApplication(wire);
}
