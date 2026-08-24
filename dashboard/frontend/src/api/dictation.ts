import { apiClient, execute } from './client';

export type DictationGrant = {
  readonly token: string;
  readonly expiresIn: number | null;
  readonly websocketUrl: string;
};

export async function requestDictationGrant(
  sampleRate: number,
  harness: string,
  workingDirectory: string,
  signal?: AbortSignal,
): Promise<DictationGrant> {
  const result = await execute(() =>
    apiClient.POST('/api/application/dictation-token', {
      body: {
        sample_rate: sampleRate,
        harness,
        working_directory:
          workingDirectory.trim().length > 0 ? workingDirectory.trim() : null,
      },
      ...(signal === undefined ? {} : { signal }),
    }),
  );
  return {
    token: result.token,
    expiresIn: result.expires_in,
    websocketUrl: result.ws_url,
  };
}
