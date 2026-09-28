# Copyright (c) 2026 Zhambyl Yermagambet
"""Name public SDK entry points used by external extension packages."""
# Vulture parses this file; Python does not execute it. Do not list private
# helpers here. The SDK architecture tests check these exact public names.

# External workers call these protocol members through the public SDK.
ExtensionLifecycle.activate
ExtensionLifecycle.deactivate
ExtensionPlugin.extension_info
ExtensionPlugin.capabilities
ExtensionCapabilities.raw_transformer
ExtensionCapabilities.canonical_transformer
ExtensionRawTransformer.transform
ExtensionDirectory.list_extensions
ExtensionHostServices.service_access

# Pydantic and external readers use these public wire fields.
DirectoryRequest.active_only
DirectorySnapshot.catalog_revision
DirectorySnapshot.runtime_revision
ServiceRevision.service_version
ContentReference.content_id
ContentReference.byte_length
RawInput.input_id
ProcessingContext.extension_id
ProcessingContext.history_revision
ProcessingContext.input_cursor
ProcessingContext.settings_revision
ExtensionInfo.api_version
ExtensionInfo.package_digest
DeactivationResult.pending_job_ids
WorkspaceScope.workspace_id
RepositoryScope.repository_id
RepositoryScope.git_directory
SnapshotCursor.projection_generation
SnapshotCursor.commit_cursor
EntryPageCursor.entry_position
Insert.output_key
CanonicalTransformRequest.prior_state
TextSpan.tone
StatusBlock.tone
FileTreeItem.tone
DiffBlock.old_path
DiffBlock.new_path
TerminalViewRequest.theme
CommandBinding.job_id
CommandBinding.request_key
MigrationBinding.candidate_id
CommandFailed.diagnostic
CommandCanceled.diagnostic
CommandOutcomeUnknown.diagnostic
CommandCancelResult.diagnostic
QueryFailed.diagnostic
QuerySnapshot.state_revision
QuerySelection.arguments_digest
SourceDescriptor.next_due_at
SourceBatch.next_due_at
TranslatedInput.verdict
IgnoredInput.verdict
UnsupportedInput.verdict
FailedInput.verdict

# External packages construct facts and schema registries and compare API versions.
ExtensionFact
SchemaSet
API_VERSION
CORE_SCHEMA_VERSION
CORE_PROJECTION_SCHEMA_VERSION
export_schema
derived_event_id
apply_canonical_transform
apply_raw_transform
derived_input_id
encode_content
activation_order
ExtensionManifest.manifest_version
ExtensionManifest.quality_policy

# Peer workers read this public wire field of a peer job stop reply.
ServiceJobCancelled.cancel_status
ActivationRequest.removed_services

# External extension tests call these public test kit entry points; pytest loads the fixture by name.
rescan
signoff
baqylau_host
# Pytest calls these hooks by name.
pytest_configure
CaseOutcomes.pytest_collection_finish
CaseOutcomes.pytest_runtest_logreport
# External extension tests call these browser, terminal, data, and runner entry points.
session_view_url
settings_url
open_pane
query
PaneReply.opened
PaneReply.focused
PaneReply.reason
workspace_view_url
repository_view_url
extension_view
terminal_view
# External extension tests build their wheel environment with this entry point.
build_environment
# External extension tests hold a scope's change stream open with this entry point.
watching
ChangeWatch.wait_for
