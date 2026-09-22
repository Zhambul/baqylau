# Copyright (c) 2026 Zhambyl Yermagambet
"""Admit user changes through checked host planning, not raw runtime proposals."""

from typing import Protocol

from baqylau_extension_api.models.base import ExtensionId

from extensions.models.lifecycle_requests import LifecyclePlan, LifecyclePlanRequest, LifecycleRequest
from extensions.models.lifecycle_state import LifecycleAdmission


class LifecycleRequestError(ValueError):
    """The requested change cannot produce a valid selected runtime."""


class LifecycleConflictError(LifecycleRequestError):
    """The caller must read changed state or confirm the complete affected set."""


class LifecycleUnavailableError(RuntimeError):
    """The manager cannot accept a new user change in its current phase."""


class ExtensionLifecycleControl(Protocol):
    """Provide preview and admission while the manager keeps resource ownership."""

    def preview_lifecycle(self, extension_id: ExtensionId, request: LifecyclePlanRequest) -> LifecyclePlan:
        """Validate the complete candidate without accepting or preparing it."""
        ...

    def change_lifecycle(self, extension_id: ExtensionId, request: LifecycleRequest) -> LifecycleAdmission:
        """Validate, confirm, and accept one user request without choosing runtime authority."""
        ...
