# Copyright (c) 2026 Zhambyl Yermagambet
"""Apply a host-selected write policy to extension management operations."""

from dataclasses import dataclass


class ExtensionReadOnlyError(PermissionError):
    """The host does not permit extension management changes in this run."""


@dataclass(frozen=True)
class ExtensionControlPolicy:
    """Leave queries and runtime restoration available when management is read-only."""

    read_only: bool = False


def require_extension_write(policy: ExtensionControlPolicy) -> None:
    """Reject changes before catalog writes, request admission, or worker preparation.

    Raises:
        ExtensionReadOnlyError: If this daemon's extension controls are read-only.

    """
    if policy.read_only:
        message = "extension management is read-only"
        raise ExtensionReadOnlyError(message)
