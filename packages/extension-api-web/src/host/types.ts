import type {
  ExtensionClient,
  ExtensionViewSnapshot,
  MountedExtensionView,
} from '../view.js';

export type ExtensionBundle = {
  readonly extensionId: string;
  readonly packageDigest: string;
  readonly moduleUrl: string;
  readonly styleUrls: readonly string[];
};

export type ViewHostOptions = {
  readonly target: HTMLElement;
  readonly origin: string;
  readonly createClient: (
    snapshot: ExtensionViewSnapshot,
    signal: AbortSignal,
  ) => ExtensionClient;
  readonly reportFailure: (error: unknown) => void;
  readonly importModule?: (url: string) => Promise<unknown>;
};

export type ViewSession = {
  readonly root: HTMLElement;
  readonly target: HTMLElement;
  readonly shadow: ShadowRoot;
  readonly controller: AbortController;
  readonly initial: ExtensionViewSnapshot;
  task: Promise<void>;
  mounted: MountedExtensionView | null;
};
