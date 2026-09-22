# Copyright (c) 2026 Zhambyl Yermagambet
"""Share exact retry and daemon ownership checks across management services."""

from collections.abc import Callable

from baqylau_extension_api.models.base import Identifier
from pydantic import ValidationError

from extensions import lifecycle_request_identity
from extensions.lifecycle_control_contract import LifecycleRequestError, LifecycleUnavailableError
from extensions.manager_contract import ExtensionManager, ManagerStateError
from extensions.models.lifecycle_operations import LifecycleProposal
from extensions.models.lifecycle_state import LifecycleAdmission
from extensions.models.management_requests import ManagementRequestOrigin


def require_manager(manager: ExtensionManager | None) -> ExtensionManager:
    """Use only a manager already owned by this daemon.

    Returns:
        The supplied owner, without starting a worker or claiming storage.

    Raises:
        LifecycleUnavailableError: If this is a request-only application.

    """
    if manager is None:
        message = "extension controls require a running daemon manager"
        raise LifecycleUnavailableError(message)
    return manager


def submit_request(
    manager: ExtensionManager, origin: ManagementRequestOrigin, prepare: Callable[[Identifier], LifecycleProposal],
) -> LifecycleAdmission:
    """Replay before planning, and recheck after a concurrent admission.

    Returns:
        Durable acceptance or the unchanged result for an exact retry.

    Raises:
        LifecycleRequestError: If planning fails and no matching request was admitted.
        LifecycleUnavailableError: If daemon ownership is unavailable.
        ManagerStateError: If the manager stops before admission.

    """
    operation_id = lifecycle_request_identity.request_operation_id(origin.request.request_id)
    existing = _replayed(manager, origin, operation_id)
    if existing is not None:
        return existing
    try:
        return manager.submit_operation(_checked_proposal(prepare, operation_id))
    except (LifecycleRequestError, LifecycleUnavailableError, ManagerStateError):
        replayed = _replayed(manager, origin, operation_id)
        if replayed is None:
            raise
        return replayed


def _checked_proposal(
    prepare: Callable[[Identifier], LifecycleProposal], operation_id: Identifier,
) -> LifecycleProposal:
    try:
        return prepare(operation_id)
    except ValidationError as error:
        message = "extension change exceeds a host limit or has an invalid candidate"
        raise LifecycleRequestError(message) from error


def _replayed(
    manager: ExtensionManager, origin: ManagementRequestOrigin, operation_id: Identifier,
) -> LifecycleAdmission | None:
    existing = manager.read_operation(operation_id)
    if existing is None:
        return None
    lifecycle_request_identity.require_same_request(existing, origin)
    return LifecycleAdmission(status="replayed", state=manager.read_state().lifecycle, operation=existing)
