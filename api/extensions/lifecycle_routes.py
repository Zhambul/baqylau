# Copyright (c) 2026 Zhambyl Yermagambet
"""Read lifecycle state and admit checked extension management requests."""

from http import HTTPStatus

from baqylau_extension_api.models.base import ExtensionId, Identifier
from fastapi import APIRouter, Depends, HTTPException

from api.extensions import lifecycle_mapper, lifecycle_models
from api.extensions.admission import require_extension_json
from api.extensions.dependencies import Manager
from api.extensions.lifecycle_admission import admission_response
from api.extensions.lifecycle_requests import LifecycleChangeRequest, LifecyclePreviewRequest
from api.responses import errors
from app.provider_extension_controls import ControlPolicy, LifecycleControlService

LIFECYCLE_RESPONSES = errors({
    403: "Extension changes are read-only or the browser origin is not accepted.",
    404: "The operation does not exist.",
    409: "State changed, work is pending, or dependent confirmation is incomplete.",
    415: "The request is not application/json.",
    503: "The daemon extension manager is unavailable.",
})
router = APIRouter(responses=LIFECYCLE_RESPONSES)


@router.get("/api/extensions/state")
def extension_runtime_state(manager: Manager, policy: ControlPolicy) -> lifecycle_models.ExtensionRuntimeResponse:
    """Read actual runtime metadata and separate stored intent.

    Returns:
        Current progress, selected revisions, and host write policy.

    """
    return lifecycle_mapper.runtime_response(manager.read_state(), policy)


@router.get("/api/extensions/operations/{operation_id}")
def extension_operation(operation_id: Identifier, manager: Manager) -> lifecycle_models.ExtensionOperationResponse:
    """Read a retained operation without returning its private settings snapshot.

    Returns:
        The requested pending or completed operation.

    Raises:
        HTTPException: If no operation has the selected identity.

    """
    operation = manager.read_operation(operation_id)
    if operation is None:
        raise HTTPException(HTTPStatus.NOT_FOUND, "extension lifecycle operation not found")
    return lifecycle_mapper.operation_response(operation)


@router.post("/api/extensions/{extension_id}/lifecycle/preview", dependencies=[Depends(require_extension_json)])
def preview_extension_lifecycle(
    extension_id: ExtensionId, lifecycle_preview_request: LifecyclePreviewRequest, control: LifecycleControlService,
) -> lifecycle_models.LifecyclePlanResponse:
    """Check a complete proposed change without accepting or preparing it.

    Returns:
        The exact affected owners for client confirmation.

    """
    return lifecycle_mapper.plan_response(control.preview_lifecycle(extension_id, lifecycle_preview_request))


@router.post(
    "/api/extensions/{extension_id}/lifecycle", dependencies=[Depends(require_extension_json)],
    status_code=HTTPStatus.ACCEPTED,
)
def change_extension_lifecycle(
    extension_id: ExtensionId, lifecycle_change_request: LifecycleChangeRequest, control: LifecycleControlService,
) -> lifecycle_models.LifecycleAdmissionResponse:
    """Accept one checked user request; successful admission is not completed activation.

    Returns:
        The new operation or the unchanged operation for an exact retry.

    """
    admitted = control.change_lifecycle(extension_id, lifecycle_change_request)
    return admission_response(admitted)
