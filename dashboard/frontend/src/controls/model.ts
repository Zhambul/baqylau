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

type InterruptControlOutcome = ControlEnvelope & {
  readonly kind: 'interrupt';
  readonly restoredText: string;
  readonly corroborated: boolean;
};

type MessageDeliveryOutcome = {
  readonly kind: 'message-delivery';
  readonly requestId: RequestId;
  readonly status: 'queued' | 'sent';
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
  | InterruptControlOutcome
  | MessageDeliveryOutcome
  | CommandControlOutcome
  | RewindControlOutcome
  | PlanChoicesControlOutcome;

export type StandardControlOutcome = Exclude<
  ControlOutcome,
  MessageDeliveryOutcome
>;

export type MessageSendOutcome = BasicControlOutcome | MessageDeliveryOutcome;

export type AttachmentReference = {
  readonly localPath: string;
  readonly displayName: string;
  readonly mediaType?: string | null;
};
