import { requestId } from '../../app/domain-ids';
import type { ControlOutcome, ControlStatus } from '../../controls/model';

export class ControlValidationFailure extends Error {
  readonly kind = 'validation';

  constructor(message: string) {
    super(message);
    this.name = 'ControlValidationFailure';
  }
}

function has(value: unknown, name: string): boolean {
  return typeof value === 'object' && value !== null && name in value;
}

function field(value: unknown, name: string): unknown {
  if (typeof value !== 'object' || value === null || !(name in value)) {
    throw new ControlValidationFailure(`control field is missing: ${name}`);
  }
  return Reflect.get(value, name);
}

function text(value: unknown, name: string): string {
  const candidate = field(value, name);
  if (typeof candidate !== 'string') {
    throw new ControlValidationFailure(`control field must be text: ${name}`);
  }
  return candidate;
}

function optionalText(value: unknown, name: string): string | null {
  const candidate = field(value, name);
  if (candidate === null || typeof candidate === 'string') return candidate;
  throw new ControlValidationFailure(
    `control field must be text or null: ${name}`,
  );
}

function flag(value: unknown, name: string): boolean {
  const candidate = field(value, name);
  if (typeof candidate !== 'boolean') {
    throw new ControlValidationFailure(
      `control field must be true or false: ${name}`,
    );
  }
  return candidate;
}

function status(value: unknown): ControlStatus {
  const candidate = text(value, 'status');
  switch (candidate) {
    case 'acknowledged':
    case 'rejected':
    case 'indeterminate':
      return candidate;
    default:
      throw new ControlValidationFailure('control has an unknown status');
  }
}

function confirmation(
  value: unknown,
): 'confirmed' | 'not_needed' | 'failed' | null {
  const candidate = optionalText(value, 'confirmation');
  switch (candidate) {
    case null:
    case 'confirmed':
    case 'not_needed':
    case 'failed':
      return candidate;
    default:
      throw new ControlValidationFailure('control has an unknown confirmation');
  }
}

export function translateControlOutcome(value: unknown): ControlOutcome {
  const deliveryStatus = text(value, 'status');
  if (deliveryStatus === 'queued' || deliveryStatus === 'sent') {
    return {
      kind: 'message-delivery',
      requestId: requestId(text(value, 'request_id')),
      status: deliveryStatus,
    };
  }
  const envelope = {
    requestId: requestId(text(value, 'request_id')),
    status: status(value),
    reason: optionalText(value, 'reason'),
  };
  if (has(value, 'corroborated')) {
    return {
      ...envelope,
      kind: 'interrupt',
      restoredText: text(value, 'restored_text'),
      corroborated: flag(value, 'corroborated'),
    };
  }
  if (has(value, 'confirmation')) {
    return {
      ...envelope,
      kind: 'command',
      confirmation: confirmation(value),
    };
  }
  if (has(value, 'degraded')) {
    return {
      ...envelope,
      kind: 'rewind',
      restoredText: text(value, 'restored_text'),
      degraded: flag(value, 'degraded'),
    };
  }
  if (has(value, 'choices')) {
    const choices = field(value, 'choices');
    if (!Array.isArray(choices)) {
      throw new ControlValidationFailure('control choices must be an array');
    }
    return {
      ...envelope,
      kind: 'plan-choices',
      choices: choices.map((choice) => ({
        digit: text(choice, 'digit'),
        label: text(choice, 'label'),
        feedback: flag(choice, 'feedback'),
      })),
    };
  }
  return { ...envelope, kind: 'basic' };
}
