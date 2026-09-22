# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep user-selected changes separate from host runtime and manager authority."""

from typing import Annotated, Literal, Self

from baqylau_extension_api.manifest.rules import require_unique
from baqylau_extension_api.models.base import Digest, ExtensionId, Identifier, WireModel
from pydantic import Field, model_validator

from extensions.models.control_requests import ControlRevisions


class LifecyclePlanRequest(ControlRevisions):
    """Pin the observed state and package bytes before preview or admission."""

    action: Literal["enable", "disable", "reload"]
    package_digest: Digest | None = None

    @model_validator(mode="after")
    def require_selected_digest(self) -> Self:
        """Require exact bytes for enable and reload, but not for removal.

        Returns:
            A request with only the authority needed for its selected action.

        Raises:
            ValueError: If the selected action and digest disagree.

        """
        if (self.action == "disable") != (self.package_digest is None):
            message = "enable and reload require a digest; disable must not select one"
            raise ValueError(message)
        return self


class LifecycleRequest(LifecyclePlanRequest):
    """Confirm affected dependents and supply an idempotent user request key."""

    request_id: Identifier
    confirmed_dependents: Annotated[tuple[ExtensionId, ...], Field(max_length=1000)] = ()

    @model_validator(mode="after")
    def require_unique_confirmation(self) -> Self:
        """Reject repeated confirmation entries before runtime planning.

        Returns:
            One checked user request, with no host-selected runtime identity.

        """
        require_unique(self.confirmed_dependents, "confirmed dependent IDs")
        return self


class LifecycleRequestOrigin(WireModel):
    """Retain the exact user request so retry never prepares a different candidate."""

    extension_id: ExtensionId
    request: LifecycleRequest


class LifecyclePlan(WireModel):
    """Describe the complete affected set without exposing private settings values."""

    extension_id: ExtensionId
    request: LifecyclePlanRequest
    affected_extensions: tuple[ExtensionId, ...]


def plan_request(request: LifecycleRequest) -> LifecyclePlanRequest:
    """Copy only preview fields, leaving request identity and confirmation private.

    Returns:
        The base request model accepted by the pure planner and public preview.

    """
    return LifecyclePlanRequest(
        action=request.action, expected_revision=request.expected_revision,
        expected_catalog_revision=request.expected_catalog_revision, package_digest=request.package_digest,
    )
