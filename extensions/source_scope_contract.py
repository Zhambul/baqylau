# Copyright (c) 2026 Zhambyl Yermagambet
"""Select host scopes independently of extension feature code."""

from contextlib import AbstractContextManager
from typing import Protocol

from baqylau_extension_api.models.scopes import ExtensionScope


class ExtensionSourceScopes(Protocol):
    """Supply complete source scopes without a fake harness or session."""

    def source_scopes(self) -> tuple[ExtensionScope, ...]:
        """Read the current complete scope set in stable order."""
        ...


class ExtensionScopeRegistry(ExtensionSourceScopes, Protocol):
    """Keep explicit host view or job scopes active for their owner's lifetime."""

    def hold_scope(self, scope: ExtensionScope) -> AbstractContextManager[None]:
        """Retain an exact scope until context exit and notify processing on set changes."""
        ...
