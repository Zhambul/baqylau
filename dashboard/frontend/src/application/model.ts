import type { SessionId } from '../app/domain-ids';

export type UsageWindow = {
  readonly key: string;
  readonly label: string;
  readonly usedPercent: string;
  readonly resetsAt: number | null;
  readonly durationMinutes: number | null;
  readonly scope: 'account' | 'model';
  readonly modelId: string | null;
};

export type UsageRow = {
  readonly harness: string;
  readonly accountId: string | null;
  readonly displayName: string;
  readonly switchable: boolean;
  readonly plan: string | null;
  readonly windows: readonly UsageWindow[];
  readonly schedulingScore: string | null;
  readonly schedulingAllowed: boolean;
  readonly limit: {
    readonly modelId: string | null;
    readonly message: string | null;
    readonly resetsAt: number | null;
  } | null;
  readonly authenticationError: string | null;
};

export type GlobalApplication = {
  readonly usageRows: readonly UsageRow[];
  readonly notifications: {
    readonly enabled: boolean;
    readonly latest: {
      readonly revision: number;
      readonly sessionId: SessionId;
      readonly kind: string;
      readonly project: string;
      readonly title: string;
    } | null;
  };
  readonly preferences: {
    readonly newSession: {
      readonly workingDirectory: string | null;
      readonly harness: string | null;
      readonly model: string | null;
      readonly effort: string | null;
    };
    readonly newSessionDrafts: readonly {
      readonly workingDirectory: string;
      readonly text: string;
      readonly sequence: number;
    }[];
    readonly hiddenDirectories: ReadonlyMap<string, number>;
    readonly limits: {
      readonly uploadBytes: number;
      readonly renameCharacters: number;
      readonly presenceSeconds: number;
    };
  };
};
