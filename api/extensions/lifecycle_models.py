# Copyright (c) 2026 Zhambyl Yermagambet
"""Expose lifecycle progress without returning private settings or runtime proposals."""

from baqylau_extension_api.models.base import ExtensionId, Identifier, Revision, WireModel
from baqylau_extension_api.models.directory import DirectorySnapshot
from baqylau_extension_api.models.lifecycle import ExtensionInfo

from api.extensions.lifecycle_requests import LifecyclePreviewRequest
from api.extensions.lifecycle_vocabulary import AdmissionStatus, LifecycleKind, RuntimePhase
from extensions.models.cleanup import RetirementIssue, ShutdownRecord
from extensions.models.lifecycle_operations import LifecycleFailure, OperationStatus, Timestamp
from extensions.models.lifecycle_selection import ExtensionIntent


class SelectedExtensionResponse(WireModel):
    """Name stored package and settings revisions without disclosing their values."""

    extension_info: ExtensionInfo
    settings_revision: Revision


class ExtensionRuntimeResponse(WireModel):
    """Separate active metadata from the last durable selection and current intent."""

    revision: Revision
    registry_revision: Revision
    phase: RuntimePhase
    active_runtime: Identifier | None
    directory: DirectorySnapshot | None
    committed_runtime: Identifier | None
    committed_packages: tuple[SelectedExtensionResponse, ...]
    pending_operation: Identifier | None
    requested: tuple[ExtensionIntent, ...]
    cleanup: tuple[RetirementIssue, ...]
    cleanup_pending: bool
    read_only: bool
    last_shutdown: ShutdownRecord | None = None


class ExtensionOperationResponse(WireModel):
    """Return an operation outcome without exposing the complete accepted proposal."""

    operation_id: Identifier
    kind: LifecycleKind
    extension_id: ExtensionId | None
    request_id: Identifier | None
    accepted_revision: Revision
    runtime_revision: Identifier
    status: OperationStatus
    created_at: Timestamp
    updated_at: Timestamp
    failure: LifecycleFailure | None


class LifecycleAdmissionResponse(WireModel):
    """Report new admission or exact retry separately from completed activation."""

    status: AdmissionStatus
    revision: Revision
    operation: ExtensionOperationResponse


class LifecyclePlanResponse(WireModel):
    """Describe affected packages without exposing a complete runtime proposal."""

    extension_id: ExtensionId
    request: LifecyclePreviewRequest
    affected_extensions: tuple[ExtensionId, ...]
