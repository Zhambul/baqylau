import { clientId, deviceId, requestId } from '../../app/domain-ids';
import type { ClientId, DeviceId, RequestId } from '../../app/domain-ids';

const DEVICE_KEY = 'baqylau-device';

function opaqueValue(): string {
  return crypto.randomUUID();
}

export function newClientId(): ClientId {
  return clientId(opaqueValue());
}

export function newRequestId(): RequestId {
  return requestId(opaqueValue());
}

export function stableDeviceId(
  storage: Storage = localStorage,
  fallback: ClientId = newClientId(),
): DeviceId {
  try {
    const stored = storage.getItem(DEVICE_KEY);
    if (stored !== null && stored.length > 0) {
      return deviceId(stored);
    }
    const created = opaqueValue();
    storage.setItem(DEVICE_KEY, created);
    return deviceId(created);
  } catch {
    return deviceId(fallback);
  }
}
