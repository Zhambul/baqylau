# Copyright (c) 2026 Zhambyl Yermagambet
"""Map internal state without copying user settings or secret document values."""

from api.extensions.lifecycle_models import (
    ExtensionOperationResponse,
    ExtensionRuntimeResponse,
    LifecyclePlanResponse,
    SelectedExtensionResponse,
)
from api.extensions.lifecycle_requests import LifecyclePreviewRequest
from api.extensions.lifecycle_vocabulary import LifecycleKind, RuntimePhase
from extensions.control_policy import ExtensionControlPolicy
from extensions.models.lifecycle_operations import LifecycleOperation
from extensions.models.lifecycle_requests import LifecyclePlan
from extensions.models.manager import ManagerSnapshot


def runtime_response(snapshot: ManagerSnapshot, policy: ExtensionControlPolicy) -> ExtensionRuntimeResponse:
    """Keep active directory metadata and committed selections distinct.

    Returns:
        Public manager state with no private settings documents.

    """
    selected = snapshot.lifecycle.committed_runtime
    return ExtensionRuntimeResponse(
        revision=snapshot.lifecycle.revision, registry_revision=snapshot.registry_revision,
        phase=RuntimePhase(snapshot.phase),
        active_runtime=snapshot.active_runtime, directory=snapshot.directory,
        committed_runtime=None if selected is None else selected.runtime_revision,
        committed_packages=() if selected is None else tuple(SelectedExtensionResponse(
            extension_info=package.extension_info, settings_revision=package.settings.revision,
        ) for package in selected.packages),
        pending_operation=snapshot.lifecycle.pending_operation, requested=snapshot.lifecycle.intents,
        cleanup=snapshot.cleanup, cleanup_pending=snapshot.cleanup_pending, read_only=policy.read_only,
        last_shutdown=snapshot.lifecycle.last_shutdown,
    )


def operation_response(operation: LifecycleOperation) -> ExtensionOperationResponse:
    """Return stable request and outcome references without the accepted candidate body.

    Returns:
        A bounded public operation report with host-selected identity.

    """
    origin = operation.proposal.request_origin
    return ExtensionOperationResponse(
        operation_id=operation.proposal.operation_id, kind=LifecycleKind(operation.proposal.kind),
        extension_id=None if origin is None else origin.extension_id,
        request_id=None if origin is None else origin.request.request_id,
        accepted_revision=operation.accepted_revision, runtime_revision=operation.proposal.candidate.runtime_revision,
        status=operation.status, created_at=operation.created_at, updated_at=operation.updated_at,
        failure=operation.failure,
    )


def plan_response(plan: LifecyclePlan) -> LifecyclePlanResponse:
    """Map a host plan to the public response contract.

    Returns:
        The selected request and affected owners, with no private state.

    """
    return LifecyclePlanResponse(
        extension_id=plan.extension_id, affected_extensions=plan.affected_extensions,
        request=LifecyclePreviewRequest.model_validate(plan.request, from_attributes=True),
    )
