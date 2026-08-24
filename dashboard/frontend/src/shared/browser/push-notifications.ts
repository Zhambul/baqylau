import {
  readPushConfiguration,
  registerPushSubscription,
} from '../../api/application';
import type { PushSubscriptionDocument } from '../../api/application';
import type { DeviceId } from '../../app/domain-ids';
import { sessionStatus } from '../../sessions/derived';
import type { SessionSnapshot } from '../../sessions/model';

const SERVER_KEY_STORAGE = 'baqylau-push-server-key';

type FailureReporter = (error: unknown) => void;

export function urlBase64Bytes(encoded: string): Uint8Array<ArrayBuffer> {
  const padding = '='.repeat((4 - (encoded.length % 4)) % 4);
  const base64 = `${encoded}${padding}`
    .replaceAll('-', '+')
    .replaceAll('_', '/');
  const raw = atob(base64);
  const bytes = new Uint8Array(new ArrayBuffer(raw.length));
  for (let index = 0; index < raw.length; index += 1)
    bytes[index] = raw.charCodeAt(index);
  return bytes;
}

export function browserNotificationPermission():
  NotificationPermission | 'unavailable' {
  return 'Notification' in window ? Notification.permission : 'unavailable';
}

export class PushNotificationController {
  private registration: ServiceWorkerRegistration | null = null;
  private sweepArmed = true;
  private started = false;

  constructor(
    private readonly deviceId: DeviceId,
    private readonly reportFailure: FailureReporter,
  ) {}

  async start(): Promise<void> {
    if (
      this.started ||
      !('serviceWorker' in navigator) ||
      !('PushManager' in window)
    )
      return;
    this.started = true;
    document.addEventListener('visibilitychange', this.visibilityChanged);
    try {
      await navigator.serviceWorker.register('/sw.js');
      this.registration = await navigator.serviceWorker.ready;
      if (browserNotificationPermission() === 'granted')
        await this.ensureSubscribed();
    } catch (error) {
      this.reportFailure(error);
    }
  }

  async requestPermission(): Promise<NotificationPermission | 'unavailable'> {
    if (!('Notification' in window)) return 'unavailable';
    const permission = await Notification.requestPermission();
    if (permission === 'granted') await this.ensureSubscribed();
    return permission;
  }

  present(sessions: readonly SessionSnapshot[]): void {
    const needingAttention = sessions.filter((snapshot) => {
      if (!snapshot.live) return false;
      const status = sessionStatus(snapshot);
      return status === 'awaiting_attention' || status === 'awaiting_response';
    });
    if ('setAppBadge' in navigator) {
      const update =
        needingAttention.length > 0
          ? navigator.setAppBadge(needingAttention.length)
          : navigator.clearAppBadge();
      void update.catch(() => undefined);
    }
    if (
      this.registration !== null &&
      this.sweepArmed &&
      document.visibilityState === 'visible'
    ) {
      this.sweepArmed = false;
      void this.sweepStale(needingAttention);
    }
  }

  destroy(): void {
    document.removeEventListener('visibilitychange', this.visibilityChanged);
    this.started = false;
  }

  private async ensureSubscribed(): Promise<void> {
    const registration = this.registration;
    if (registration === null || browserNotificationPermission() !== 'granted')
      return;
    const configuration = await readPushConfiguration();
    if (!configuration.enabled || configuration.key === null) return;
    try {
      let subscription = await registration.pushManager.getSubscription();
      const rememberedKey = readStoredKey();
      if (subscription !== null && rememberedKey !== configuration.key) {
        await subscription.unsubscribe();
        subscription = null;
      }
      subscription ??= await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64Bytes(configuration.key),
      });
      const document = subscriptionDocument(subscription);
      storeKey(configuration.key);
      await registerPushSubscription(document, this.deviceId, deviceLabel());
    } catch (error) {
      this.reportFailure(error);
    }
  }

  private async sweepStale(
    needingAttention: readonly SessionSnapshot[],
  ): Promise<void> {
    const registration = this.registration;
    if (registration === null) return;
    try {
      const needed = new Set<string>(
        needingAttention.map((snapshot) => snapshot.session.sessionId),
      );
      const notifications = await registration.getNotifications();
      for (const notification of notifications) {
        const sessionId = notificationSessionId(notification);
        if (sessionId === null || !needed.has(sessionId)) notification.close();
      }
    } catch {
      // Notification cleanup is best-effort.
    }
  }

  private visibilityChanged = (): void => {
    if (document.visibilityState !== 'visible') this.sweepArmed = true;
  };
}

function subscriptionDocument(
  subscription: PushSubscription,
): PushSubscriptionDocument {
  const json = subscription.toJSON();
  const endpoint = json.endpoint;
  const p256dh = json.keys?.p256dh;
  const auth = json.keys?.auth;
  if (
    typeof endpoint !== 'string' ||
    typeof p256dh !== 'string' ||
    typeof auth !== 'string'
  )
    throw new Error('browser returned an incomplete push subscription');
  return { endpoint, keys: { p256dh, auth } };
}

function notificationSessionId(notification: Notification): string | null {
  const data: unknown = notification.data;
  if (typeof data !== 'object' || data === null || !('session_id' in data))
    return null;
  const sessionId: unknown = Reflect.get(data, 'session_id');
  return typeof sessionId === 'string' && sessionId.length > 0
    ? sessionId
    : null;
}

function readStoredKey(): string {
  try {
    return localStorage.getItem(SERVER_KEY_STORAGE) ?? '';
  } catch {
    return '';
  }
}

function storeKey(key: string): void {
  try {
    localStorage.setItem(SERVER_KEY_STORAGE, key);
  } catch {
    // A subscription still works when private storage is unavailable.
  }
}

function deviceLabel(): string {
  const userAgentData: unknown = Reflect.get(navigator, 'userAgentData');
  const reportedPlatform =
    typeof userAgentData === 'object' &&
    userAgentData !== null &&
    'platform' in userAgentData
      ? Reflect.get(userAgentData, 'platform')
      : null;
  return (
    (typeof reportedPlatform === 'string' ? reportedPlatform : '') ||
    navigator.userAgent ||
    'device'
  ).slice(0, 60);
}
