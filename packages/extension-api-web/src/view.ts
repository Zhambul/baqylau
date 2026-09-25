import type {
  DirectoryRequest,
  DirectorySnapshot,
  EncodedDocument,
  ExtensionScope,
  Diagnostic,
  QueryPageCursor,
  QueryResult,
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

/** The accepted settings of the view's own extension in the view's scope. */
export type ViewSettingsState = {
  readonly settingsRevision: number;
  readonly override: EncodedDocument | null;
  readonly effective: EncodedDocument;
};

/** The host accepted a settings change; it applies when the operation ends. */
export type SettingsSaveResult = {
  readonly operationId: string;
};

export type CommandJobState =
  | 'accepted'
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'canceled'
  | 'outcome_unknown';

/**
 * One durable job of the view's extension. A succeeded job has the command's
 * result document; a failed, canceled, or unknown job has its diagnostic.
 */
export type CommandJob = {
  readonly jobId: string;
  readonly state: CommandJobState;
  readonly document: EncodedDocument | null;
  readonly diagnostic: Diagnostic | null;
};

/** Every method acts only on the view's own extension and scope. */
export type ExtensionClient = {
  readonly listExtensions: (
    request: DirectoryRequest,
  ) => Promise<DirectorySnapshot>;
  readonly readSettings: () => Promise<ViewSettingsState>;
  /** Replace the scope's override, or reset it with null; stale revisions fail. */
  readonly saveSettings: (
    document: EncodedDocument | null,
    expectedSettingsRevision: number,
  ) => Promise<SettingsSaveResult>;
  /**
   * Run one declared query with JSON arguments. The host checks the arguments
   * and the result against the query's schemas; a failed read is a result.
   */
  readonly query: (
    queryId: string,
    argumentsJson: string,
    page?: QueryPageCursor,
  ) => Promise<QueryResult>;
  /**
   * Call the listener after the extension's records change. The watch holds
   * the scope, so its sources stay active. It stops with the returned function
   * or with the view's signal.
   */
  readonly watchChanges: (listener: () => void) => () => void;
  /**
   * Submit one declared command. The same request key gives the same job, so
   * a retry after a lost reply does not run the write again. A write names the
   * state that it expects; the extension refuses stale work.
   */
  readonly runCommand: (
    commandId: string,
    requestKey: string,
    argumentsJson: string,
    expectedStateRevision: string | null,
  ) => Promise<CommandJob>;
  /** Read one job of the extension in the view's scope. */
  readonly readJob: (jobId: string) => Promise<CommandJob>;
};

/**
 * Name the feed entry that a replacement view shows in place of its row. For
 * an entry of the package's own type, `kind` is that entry type and `document`
 * is the entry's JSON document.
 */
export type FeedEntrySubject = {
  readonly entryId: string;
  readonly kind: string;
  readonly document?: string;
};

export type ExtensionViewSnapshot = {
  readonly extensionId: string;
  readonly viewId: string;
  readonly scope: ExtensionScope;
  readonly runtimeRevision: string;
  readonly settingsRevision: number;
  readonly settings: EncodedDocument | null;
  readonly theme: ThemeValues;
  readonly subject?: FeedEntrySubject;
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
