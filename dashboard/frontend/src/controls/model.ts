import type { RequestId } from '../app/domain-ids';
import type { PlanChoice } from './plan-choice';

export type ControlStatus = 'acknowledged' | 'rejected' | 'indeterminate';

type ControlEnvelope = {
  readonly requestId: RequestId;
  readonly status: ControlStatus;
  readonly reason: string | null;
};

type BasicControlOutcome = ControlEnvelope & {
  readonly kind: 'basic';
};

type DeliveryControlOutcome = ControlEnvelope & {
  readonly kind: 'delivery';
  readonly queued: boolean;
  readonly restoredText: string;
  readonly corroborated: boolean;
};

type CommandControlOutcome = ControlEnvelope & {
  readonly kind: 'command';
  readonly confirmation: 'confirmed' | 'not_needed' | 'failed' | null;
};

type RewindControlOutcome = ControlEnvelope & {
  readonly kind: 'rewind';
  readonly restoredText: string;
  readonly degraded: boolean;
};

type PlanChoicesControlOutcome = ControlEnvelope & {
  readonly kind: 'plan-choices';
  readonly choices: readonly PlanChoice[];
};

export type ControlOutcome =
  | BasicControlOutcome
  | DeliveryControlOutcome
  | CommandControlOutcome
  | RewindControlOutcome
  | PlanChoicesControlOutcome;

export type AttachmentReference = {
  readonly localPath: string;
  readonly displayName: string;
  readonly mediaType?: string | null;
};
