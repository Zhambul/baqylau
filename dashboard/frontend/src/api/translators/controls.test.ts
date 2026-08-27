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
        restored_text: '',
        corroborated: false,
      }),
    ).toMatchObject({ kind: 'interrupt', status: 'indeterminate' });

    expect(
      translateControlOutcome({
        request_id: 'request-four',
        status: 'queued',
      }),
    ).toEqual({
      kind: 'message-delivery',
      requestId: 'request-four',
      status: 'queued',
    });
  });

  it('rejects malformed extended outcomes', () => {
    expect(() =>
      translateControlOutcome({
        request_id: 'request-five',
        status: 'acknowledged',
        reason: null,
        restored_text: '',
        corroborated: 'no',
      }),
    ).toThrow(ControlValidationFailure);
  });
});
