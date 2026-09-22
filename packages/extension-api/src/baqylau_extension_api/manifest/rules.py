# Copyright (c) 2026 Zhambyl Yermagambet
"""Apply common identity rules to data-only declarations."""

from collections.abc import Hashable, Iterable

from baqylau_extension_api.errors import ExtensionContractError


def require_unique(names: Iterable[Hashable], label: str) -> None:
    """Reject repeated identities instead of selecting a declaration by order.

    Raises:
        ExtensionContractError: If an identity occurs more than once.

    """
    sequence = tuple(names)
    if len(set(sequence)) != len(sequence):
        message = f"{label} must be unique"
        raise ExtensionContractError(message)


def require_owned(names: Iterable[str], owner: str) -> None:
    """Require registered contribution IDs to use their package namespace.

    Raises:
        ExtensionContractError: If an ID claims another owner.

    """
    prefix = f"{owner}."
    invalid = any(
        not name.startswith(prefix) or name == prefix
        for name in names
    )
    if invalid:
        message = "contribution identities must use their extension namespace"
        raise ExtensionContractError(message)
