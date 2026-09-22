import type {
  DirectoryRequest,
  DirectorySnapshot,
  EncodedDocument,
  ExtensionScope,
} from './wire.js';

export type ThemeValues = {
  readonly mode: 'dark' | 'light';
  readonly background: string;
  readonly foreground: string;
  readonly muted: string;
  readonly accent: string;
  readonly fontFamily: string;
  readonly fontSize: string;
};

/** This draft client will gain the typed query and command contracts in P01. */
export type ExtensionClient = {
  readonly listExtensions: (
    request: DirectoryRequest,
  ) => Promise<DirectorySnapshot>;
};

export type ExtensionViewSnapshot = {
  readonly extensionId: string;
  readonly viewId: string;
  readonly scope: ExtensionScope;
  readonly runtimeRevision: string;
  readonly settingsRevision: number;
  readonly settings: EncodedDocument | null;
  readonly theme: ThemeValues;
};

export type ExtensionViewContext = ExtensionViewSnapshot & {
  readonly api: ExtensionClient;
  readonly signal: AbortSignal;
};

export type MountedExtensionView = {
  update(context: ExtensionViewContext): void | Promise<void>;
  dispose(): void | Promise<void>;
};

/** Each package exports its own mount method and owns its component runtime. */
export type ExtensionWebModule = {
  mount(
    target: HTMLElement,
    context: ExtensionViewContext,
  ): MountedExtensionView | Promise<MountedExtensionView>;
};
