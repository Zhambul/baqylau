# Copyright (c) 2026 Zhambyl Yermagambet
"""Resolve a declared secret reference through the host at call time."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from baqylau_extension_api.models.credentials import SecretRequest, SecretResult


class ExtensionCredentialService(Protocol):
    """Read secret values that the user stored; settings documents never contain them."""

    def resolve_secret(self, secret_request: SecretRequest) -> SecretResult:
        """Read the current value of one declared secret reference, or report that it is missing."""
        ...
