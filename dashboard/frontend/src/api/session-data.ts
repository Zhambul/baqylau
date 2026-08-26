import type { SessionId } from '../app/domain-ids';
import type { SessionList, SessionSnapshot } from '../sessions/model';
import { apiClient, execute } from './client';
import {
  translateSessionList,
  translateSessionSnapshot,
} from './translators/session-data';

export async function readSessionList(
  signal?: AbortSignal,
): Promise<SessionList> {
  const wire = await execute(() =>
    apiClient.GET('/sessionData', {
      ...(signal === undefined ? {} : { signal }),
    }),
  );
  return translateSessionList(wire);
}

export async function readSessionDirectories(
  signal?: AbortSignal,
): Promise<readonly string[]> {
  return execute(() =>
    apiClient.GET('/sessionData/directories', {
      ...(signal === undefined ? {} : { signal }),
    }),
  );
}

export async function readSession(
  id: SessionId,
  signal?: AbortSignal,
): Promise<SessionSnapshot> {
  const wire = await execute(() =>
    apiClient.GET('/sessionData/{session_id}', {
      params: { path: { session_id: id } },
      ...(signal === undefined ? {} : { signal }),
    }),
  );
  return translateSessionSnapshot(wire);
}
