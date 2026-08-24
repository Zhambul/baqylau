import type { ClientId } from '../app/domain-ids';

export type ViewMode = 'verbose' | 'default' | 'focus';

export type SessionApplication = {
  readonly preferences: {
    readonly viewMode: ViewMode;
    readonly notificationsMuted: boolean;
    readonly tasksHidden: boolean;
  };
  readonly composer: {
    readonly draft: {
      readonly text: string;
      readonly origin: ClientId;
      readonly sequence: number;
    } | null;
    readonly queue: {
      readonly items: readonly { readonly text: string }[];
      readonly origin: ClientId;
    } | null;
  };
  readonly dialog: {
    readonly draft: {
      readonly attentionId: string;
      readonly answers: readonly {
        readonly selected: readonly string[];
        readonly other: string;
      }[];
      readonly origin: ClientId;
    } | null;
  };
  readonly terminal: {
    readonly windowId: string | null;
    readonly inputState: {
      readonly typedText: string | null;
      readonly suggestion: string | null;
    } | null;
  };
  readonly errors: readonly {
    readonly errorId: number;
    readonly timestamp: number;
    readonly component: string;
    readonly action: string;
    readonly traceback: string;
    readonly context: string;
  }[];
};
