# Copyright (c) 2026 Zhambyl Yermagambet
"""Drive lifecycle requests through the real daemon's typed HTTP SDK."""

from typing import Literal

from api.extensions.lifecycle_models import ExtensionOperationResponse
from api.extensions.lifecycle_requests import LifecycleChangeRequest
from sdk.client import BaqylauClient
from tests import terminal_pty_waits

# Private worker preparation builds a package environment. It needs more time
# than a PTY window check under a loaded test machine.
OPERATION_TIMEOUT_SECONDS = 120.0


def lifecycle_request(
    client: BaqylauClient, owner: str, action: Literal["enable", "disable", "reload"], request_id: str,
) -> LifecycleChangeRequest:
    """Wait for the actual startup boundary, then select the observed client revisions.

    Returns:
        A complete user request without manager or runtime authority.

    """
    terminal_pty_waits.wait_until(
        lambda: client.extensions.lifecycle.state().phase == "running", OPERATION_TIMEOUT_SECONDS,
    )
    catalog = client.extensions.catalog()
    digest = None if action == "disable" else next(
        entry.package_digest for entry in catalog.entries if entry.extension_id == owner
    )
    return LifecycleChangeRequest(
        action=action, request_id=request_id, expected_revision=client.extensions.lifecycle.state().revision,
        expected_catalog_revision=catalog.revision, package_digest=digest,
    )


def wait_operation(client: BaqylauClient, operation_id: str) -> ExtensionOperationResponse:
    """Poll a known admitted operation until it has a terminal stored result.

    Returns:
        The actual operation result, never a guessed completion from HTTP admission.

    """
    terminal_pty_waits.wait_until(
        lambda: client.extensions.lifecycle.operation(operation_id).status != "preparing",
        OPERATION_TIMEOUT_SECONDS,
    )
    terminal_pty_waits.wait_until(
        lambda: not client.extensions.lifecycle.state().cleanup_pending, OPERATION_TIMEOUT_SECONDS,
    )
    return client.extensions.lifecycle.operation(operation_id)


def active_owners(client: BaqylauClient) -> set[str]:
    """Read the enabled owners of the active runtime.

    Returns:
        The owner IDs.

    """
    directory = client.extensions.lifecycle.state().directory
    entries = () if directory is None else directory.entries
    return {entry.extension_info.extension_id for entry in entries if entry.state == "enabled"}
