export type SessionCapabilities = {
  readonly send: boolean;
  readonly interrupt: boolean;
  readonly background: boolean;
  readonly close: boolean;
  readonly rename: boolean;
  readonly autoname: boolean;
  readonly rewind: boolean;
  readonly compact: boolean;
  readonly model: boolean;
  readonly effort: boolean;
  readonly answer: boolean;
  readonly plan: boolean;
};

export type HarnessDescription = {
  readonly name: string;
  readonly displayName: string;
  readonly launchable: boolean;
  readonly defaultForLaunch: boolean;
  readonly supportsAttachments: boolean;
  readonly supportsAccounts: boolean;
  readonly supportsTerminalInput: boolean;
  readonly supportsReadableCompactionContext: boolean;
  readonly requiresInitialMessage: boolean;
  readonly capabilities: SessionCapabilities;
};

export type HarnessCatalog = {
  readonly commands: readonly {
    readonly command: string;
    readonly description: string;
    readonly minimumPromptCount: number;
  }[];
  readonly models: readonly {
    readonly modelId: string;
    readonly displayName: string;
    readonly default: boolean;
    readonly efforts: readonly {
      readonly value: string;
      readonly displayName: string;
      readonly default: boolean;
    }[];
  }[];
  readonly rewindModes: readonly {
    readonly value: string;
    readonly displayName: string;
  }[];
};
