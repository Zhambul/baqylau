import type { SessionId } from '../app/domain-ids';
import type { HarnessCatalog, HarnessDescription } from '../harnesses/model';
import { apiClient, execute } from './client';
import { translateCatalog, translateHarness } from './translators/harnesses';

export async function readHarnesses(
  signal?: AbortSignal,
): Promise<readonly HarnessDescription[]> {
  const wire = await execute(() =>
    apiClient.GET('/api/harnesses', {
      ...(signal === undefined ? {} : { signal }),
    }),
  );
  return wire.map(translateHarness);
}

export async function readHarnessCatalog(
  harness: string,
  sessionId: SessionId,
  workingDirectory: string,
  signal?: AbortSignal,
): Promise<HarnessCatalog> {
  const wire = await execute(() =>
    apiClient.GET('/api/harnesses/{harness}/catalog', {
      params: {
        path: { harness },
        query: {
          session_id: sessionId,
          working_directory: workingDirectory,
        },
      },
      ...(signal === undefined ? {} : { signal }),
    }),
  );
  return translateCatalog(wire);
}
