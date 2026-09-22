# Copyright (c) 2026 Zhambyl Yermagambet
"""Reject calls from another prepared worker runtime."""

from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.models.environment import ExtensionEnvironment


def require_runtime_revision(revision: str, environment: ExtensionEnvironment) -> None:
    """Keep stale inner requests out of a valid outer transport channel.

    Raises:
        ExtensionContractError: If a call names another prepared runtime.

    """
    if revision != environment.runtime_revision:
        message = "capability request or result has a stale runtime revision"
        raise ExtensionContractError(message)
