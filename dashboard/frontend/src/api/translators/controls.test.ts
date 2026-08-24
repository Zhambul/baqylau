import { describe, expect, it } from 'vitest';

import { ControlValidationFailure, translateControlOutcome } from './controls';

describe('control outcome translator', () => {
  it('keeps acknowledged, rejected, and indeterminate verdicts distinct', () => {
    expect(
      translateControlOutcome({
        request_id: 'request-one',
        status: 'acknowledged',
        reason: null,
      }),
    ).toMatchObject({ kind: 'basic', status: 'acknowledged' });

    expect(
      translateControlOutcome({
        request_id: 'request-two',
        status: 'rejected',
        reason: 'busy',
      }),
    ).toMatchObject({ status: 'rejected', reason: 'busy' });

    expect(
      translateControlOutcome({
        request_id: 'request-three',
        status: 'indeterminate',
        reason: null,
        queued: false,
        restored_text: '',
        corroborated: false,
      }),
    ).toMatchObject({ kind: 'delivery', status: 'indeterminate' });
  });

  it('rejects malformed extended outcomes', () => {
    expect(() =>
      translateControlOutcome({
        request_id: 'request-four',
        status: 'acknowledged',
        reason: null,
        queued: 'no',
      }),
    ).toThrow(ControlValidationFailure);
  });
});
