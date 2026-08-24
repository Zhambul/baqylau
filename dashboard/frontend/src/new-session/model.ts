import type { SessionId } from '../app/domain-ids';
import type { AttachmentReference } from '../controls/model';

export type ResumableSession = {
  readonly sessionId: SessionId;
  readonly title: string | null;
  readonly lastActivityAt: number;
  readonly active: boolean;
  readonly harness: string;
  readonly model: {
    readonly id: string;
    readonly displayName: string;
  } | null;
  readonly effort: string | null;
  readonly account: {
    readonly id: string;
    readonly displayName: string;
  } | null;
};

export type LaunchInput = {
  readonly harness: string;
  readonly workingDirectory: string;
  readonly initialText: string | null;
  readonly modelId: string | null;
  readonly effort: string | null;
  readonly accountId: string | null;
  readonly resumeSessionId: SessionId | null;
  readonly attachments: readonly AttachmentReference[];
};

export type LaunchDisplay = {
  readonly mode: 'new' | 'resume';
  readonly toolLabel: string;
  readonly model: string;
  readonly effort: string;
  readonly account: string;
  readonly prompt: string;
};

export type NewSessionSeed = {
  readonly workingDirectory: string;
  readonly harness: string;
  readonly modelId: string;
  readonly effort: string;
  readonly accountId: string;
  readonly prompt: string;
  readonly resumeSessionId: SessionId | null;
  readonly attachments: readonly AttachmentReference[];
};

export type LaunchResult = {
  readonly status: 'started' | 'rejected';
  readonly windowId: string | null;
  readonly reason: string | null;
};
