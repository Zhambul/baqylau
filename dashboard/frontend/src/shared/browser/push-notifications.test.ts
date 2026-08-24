import { describe, expect, it } from 'vitest';

import { urlBase64Bytes } from './push-notifications';

describe('push notification helpers', () => {
  it('decodes an unpadded base64url server key', () => {
    expect([...urlBase64Bytes('AQID-_8')]).toEqual([1, 2, 3, 251, 255]);
  });
});
