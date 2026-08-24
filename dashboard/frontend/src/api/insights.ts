import type { ApplicationInsights } from '../stats/model';
import { apiClient, execute } from './client';
import { translateInsights } from './translators/insights';

export async function readInsights(
  signal?: AbortSignal,
): Promise<ApplicationInsights> {
  const wire = await execute(() =>
    apiClient.GET('/api/insights', {
      ...(signal === undefined ? {} : { signal }),
    }),
  );
  return translateInsights(wire);
}
