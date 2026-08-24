import type { ClientId, DeviceId, SessionId } from '../app/domain-ids';
import { HttpFailure, apiClient, messageFrom, request } from './client';

type TelemetryValue = string | number | boolean | null;
export type TelemetryFields = Readonly<Record<string, TelemetryValue>>;
export type OptimisticAction = 'answer' | 'close' | 'composer' | 'plan';

export type BrowserEvent = {
  readonly timestamp: number;
  readonly sessionId: SessionId | null;
  readonly name: string;
  readonly details: TelemetryFields;
};

export type BrowserEventBatch = {
  readonly clientId: ClientId;
  readonly deviceId: DeviceId;
  readonly connection: TelemetryFields;
  readonly events: readonly BrowserEvent[];
};

export async function deliverBrowserEvents(
  batch: BrowserEventBatch,
): Promise<'delivered' | 'rejected'> {
  const result = await request(() =>
    apiClient.POST('/api/application/browser-events', {
      body: {
        client_id: batch.clientId,
        device_id: batch.deviceId,
        connection: batch.connection,
        events: batch.events.map((event) => ({
          timestamp: event.timestamp,
          session_id: event.sessionId,
          name: event.name,
          details: event.details,
        })),
      },
    }),
  );
  if (result.data !== undefined) return 'delivered';
  if (result.response.status >= 400 && result.response.status < 500)
    return 'rejected';
  throw new HttpFailure(result.response.status, messageFrom(result.error));
}

export async function recordOptimisticAction(
  sessionId: SessionId,
  action: OptimisticAction,
  phase: 'shown' | 'reconciled' | 'dropped' | 'stale',
  elapsedMilliseconds: number,
  characterCount: number | null,
  reason: string | null,
): Promise<void> {
  const result = await request(() =>
    apiClient.POST(
      '/api/sessions/{session_id}/application/optimistic-actions',
      {
        params: { path: { session_id: sessionId } },
        body: {
          action,
          phase,
          elapsed_milliseconds: elapsedMilliseconds,
          character_count: characterCount,
          reason,
        },
      },
    ),
  );
  if (result.data === undefined)
    throw new HttpFailure(result.response.status, messageFrom(result.error));
}

export async function recordClientFailure(
  sessionId: SessionId,
  gesture: string,
  failureKind: 'transport' | 'http',
  error: string,
  statusCode: number | null,
  characterCount: number | null,
): Promise<void> {
  const result = await request(() =>
    apiClient.POST('/api/sessions/{session_id}/application/client-failures', {
      params: { path: { session_id: sessionId } },
      body: {
        gesture,
        failure_kind: failureKind,
        error,
        status_code: statusCode,
        character_count: characterCount,
      },
    }),
  );
  if (result.data === undefined)
    throw new HttpFailure(result.response.status, messageFrom(result.error));
}
