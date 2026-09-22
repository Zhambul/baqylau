import type { components } from './generated/wire.js';

export type SessionScope = components['schemas']['SessionScope'];
export type WorkspaceScope = components['schemas']['WorkspaceScope'];
export type RepositoryScope = components['schemas']['RepositoryScope'];
export type InstallationScope = components['schemas']['InstallationScope'];
export type ExtensionScope =
  SessionScope | WorkspaceScope | RepositoryScope | InstallationScope;
export type EncodedDocument = components['schemas']['EncodedDocument'];
export type SchemaRef = components['schemas']['SchemaRef'];
export type SchemaDefinition = components['schemas']['SchemaDefinition'];
export type ContentReference = components['schemas']['ContentReference'];
export type DirectoryRequest = components['schemas']['DirectoryRequest'];
export type DirectorySnapshot = components['schemas']['DirectorySnapshot'];
export type SnapshotCursor = components['schemas']['SnapshotCursor'];
export type EntryPageCursor = components['schemas']['EntryPageCursor'];
export type TerminalViewRequest = components['schemas']['TerminalViewRequest'];
export type TerminalView = components['schemas']['TerminalView'];
export type TerminalBlock = TerminalView['blocks'][number];
export type OperationBinding = components['schemas']['OperationBinding'];
export type QueryRequest = components['schemas']['QueryRequest'];
export type QueryReady = components['schemas']['QueryReady'];
export type QueryFailed = components['schemas']['QueryFailed'];
export type QueryResult = QueryReady | QueryFailed;
export type QuerySnapshot = components['schemas']['QuerySnapshot'];
export type QuerySelection = components['schemas']['QuerySelection'];
export type QueryPageCursor = components['schemas']['QueryPageCursor'];
export type CommandBinding = components['schemas']['CommandBinding'];
export type CommandRequest = components['schemas']['CommandRequest'];
export type CommandCancelRequest =
  components['schemas']['CommandCancelRequest'];
export type CommandCancelResult = components['schemas']['CommandCancelResult'];
export type CommandReconcileRequest =
  components['schemas']['CommandReconcileRequest'];
export type CommandSucceeded = components['schemas']['CommandSucceeded'];
export type CommandFailed = components['schemas']['CommandFailed'];
export type CommandCanceled = components['schemas']['CommandCanceled'];
export type CommandOutcomeUnknown =
  components['schemas']['CommandOutcomeUnknown'];
export type CommandResult =
  CommandSucceeded | CommandFailed | CommandCanceled | CommandOutcomeUnknown;
export type ObservationCandidate =
  components['schemas']['ObservationCandidate'];
