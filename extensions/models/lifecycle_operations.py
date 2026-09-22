# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep durable lifecycle requests and outcomes separate from worker health."""

from typing import Annotated, Literal, Self

from baqylau_extension_api.manifest.rules import require_unique
from baqylau_extension_api.models.base import Identifier, NonemptyText, Revision, WireModel
from pydantic import Field, FiniteFloat, model_validator

from extensions.models.lifecycle_selection import ExtensionIntent, SettingsChange
from extensions.models.management_requests import ManagementRequestOrigin
from extensions.models.runtime_candidates import MigratingRuntimeSelection, RuntimeCandidate
from extensions.models.runtime_resolution import RuntimeResolution

MAX_OPERATION_BYTES = 8_388_608
Timestamp = Annotated[FiniteFloat, Field(ge=0)]
type OperationStatus = Literal["preparing", "succeeded", "failed", "interrupted"]


class LifecycleProposal(WireModel):
    """Pin the complete candidate and requested changes before any worker call."""

    operation_id: Identifier
    manager_id: Identifier
    expected_revision: Revision
    kind: Literal["enable", "disable", "reload", "settings", "restore", "failure"]
    candidate: RuntimeCandidate
    intents: Annotated[tuple[ExtensionIntent, ...], Field(max_length=1000)] = ()
    settings_changes: Annotated[tuple[SettingsChange, ...], Field(max_length=1000)] = ()
    request_origin: ManagementRequestOrigin | None = None

    @model_validator(mode="after")
    def require_bounded_selection(self) -> Self:
        """Reject repeated writes and oversized persisted operation documents.

        Returns:
            One complete, bounded operation request.

        Raises:
            ValueError: If its encoded form exceeds the host document bound.

        """
        require_unique((entry.extension_id for entry in self.intents), "requested extension IDs")
        require_unique((entry.extension_id for entry in self.settings_changes), "settings owner IDs")
        if len(self.model_dump_json().encode()) > MAX_OPERATION_BYTES:
            message = "extension lifecycle operation exceeds its document limit"
            raise ValueError(message)
        return self


class LifecycleFailure(WireModel):
    """Store a bounded reason, not feature log text or secret settings."""

    code: Literal["preparation_failed", "incompatible", "unresolved_jobs", "interrupted", "policy_denied"]
    detail: Annotated[NonemptyText, Field(max_length=1000)]


class LifecycleOperation(WireModel):
    """Retain one accepted request and its terminal outcome across daemon restarts."""

    proposal: LifecycleProposal
    accepted_revision: Revision
    status: OperationStatus
    created_at: Timestamp
    updated_at: Timestamp
    failure: LifecycleFailure | None = None
    resolution: RuntimeResolution | None = None

    @model_validator(mode="after")
    def require_consistent_outcome(self) -> Self:
        """Require a reason exactly when the operation did not succeed.

        Returns:
            A valid pending or completed operation record.

        Raises:
            ValueError: If state, timestamps, or acceptance revision disagree.

        """
        if (
            (self.status in {"failed", "interrupted"}) != (self.failure is not None)
            or self.updated_at < self.created_at or self.accepted_revision != self.proposal.expected_revision + 1
        ):
            message = "extension operation outcome does not match its accepted request"
            raise ValueError(message)
        _require_resolution(self.proposal, self.status, self.resolution)
        return self


class LifecycleCompletion(WireModel):
    """Finish the stored candidate; late callers cannot substitute another set."""

    manager_id: Identifier
    operation_id: Identifier
    expected_revision: Revision
    completed_at: Timestamp
    failure: LifecycleFailure | None = None
    resolution: RuntimeResolution | None = None


def _require_resolution(
    proposal: LifecycleProposal, status: OperationStatus, resolution: RuntimeResolution | None,
) -> None:
    required = status == "succeeded" and isinstance(proposal.candidate, MigratingRuntimeSelection)
    if required != (resolution is not None):
        message = "only a successful migration operation requires resolved settings"
        raise ValueError(message)
    if resolution is not None and isinstance(proposal.candidate, MigratingRuntimeSelection):
        resolution.validate_candidate(proposal.candidate)
