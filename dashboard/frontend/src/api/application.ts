import type { GlobalApplication } from '../application/model';
import type { DeviceId, SessionId } from '../app/domain-ids';
import { apiClient, execute } from './client';
import { translateGlobalApplication } from './translators/application';

export type PushConfiguration = {
  readonly enabled: boolean;
  readonly key: string | null;
};

export type PushSubscriptionDocument = {
  readonly endpoint: string;
  readonly keys: {
    readonly p256dh: string;
    readonly auth: string;
  };
};

export async function readGlobalApplication(
  signal?: AbortSignal,
): Promise<GlobalApplication> {
  const wire = await execute(() =>
    apiClient.GET('/api/application', {
      ...(signal === undefined ? {} : { signal }),
    }),
  );
  return translateGlobalApplication(wire);
}

export async function setGlobalNotifications(enabled: boolean): Promise<void> {
  await execute(() =>
    apiClient.POST('/api/application/notifications', {
      body: { enabled },
    }),
  );
}

export async function hideDirectory(
  workingDirectory: string,
): Promise<ReadonlyMap<string, number>> {
  const response = await execute(() =>
    apiClient.POST('/api/application/hidden-directories', {
      body: { working_directory: workingDirectory },
    }),
  );
  return new Map(Object.entries(response.hidden));
}

export async function readPushConfiguration(): Promise<PushConfiguration> {
  return execute(() => apiClient.GET('/api/application/push-configuration'));
}

export async function registerPushSubscription(
  subscription: PushSubscriptionDocument,
  deviceId: DeviceId,
  deviceLabel: string,
): Promise<void> {
  await execute(() =>
    apiClient.POST('/api/application/push-subscriptions', {
      body: {
        subscription: {
          endpoint: subscription.endpoint,
          keys: {
            p256dh: subscription.keys.p256dh,
            auth: subscription.keys.auth,
          },
        },
        device_id: deviceId,
        device_label: deviceLabel,
      },
    }),
  );
}

export async function reportPresence(
  deviceId: DeviceId,
  sessionId: SessionId | null,
  away: boolean,
): Promise<void> {
  await execute(() =>
    apiClient.POST('/api/application/presence', {
      body: {
        device_id: deviceId,
        session_id: sessionId,
        away,
      },
    }),
  );
}
