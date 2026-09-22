# Copyright (c) 2026 Zhambyl Yermagambet
"""Use the same accepted and refused response for all lifecycle operations."""

from http import HTTPStatus

from fastapi import HTTPException

from api.extensions.lifecycle_mapper import operation_response
from api.extensions.lifecycle_models import LifecycleAdmissionResponse
from api.extensions.lifecycle_vocabulary import AdmissionStatus
from extensions.models.lifecycle_state import LifecycleAdmission


def admission_response(admitted: LifecycleAdmission) -> LifecycleAdmissionResponse:
    """Keep acceptance distinct from completion for lifecycle and settings writes.

    Returns:
        A new or replayed operation, not its private proposal.

    Raises:
        HTTPException: If the selected state changed or another operation is pending.

    """
    if admitted.status in {"stale", "busy"} or admitted.operation is None:
        raise HTTPException(HTTPStatus.CONFLICT, "extension state changed or another lifecycle operation is pending")
    return LifecycleAdmissionResponse(
        status=AdmissionStatus(admitted.status), revision=admitted.state.revision,
        operation=operation_response(admitted.operation),
    )
