# Copyright (c) 2026 Zhambyl Yermagambet
"""Describe one worker identity and its activation boundary."""

from typing import Annotated, Literal

from pydantic import Field

from baqylau_extension_api.models.base import Digest, ExtensionId, Identifier, NonemptyText, Revision, WireModel
from baqylau_extension_api.models.documents import EncodedDocument
from baqylau_extension_api.versions import PackageVersion


class ExtensionInfo(WireModel):
    """Identify the exact package and public API used by a worker."""

    extension_id: ExtensionId
    package_version: PackageVersion
    api_version: PackageVersion
    package_digest: Digest


class ActivationRequest(WireModel):
    """Prepare one runtime revision before the host makes it active."""

    runtime_revision: Identifier
    settings_revision: Revision
    settings: EncodedDocument | None = None


class ActivationReady(WireModel):
    """Confirm that the worker can serve the prepared runtime revision."""

    kind: Literal["ready"] = "ready"
    runtime_revision: Identifier


class ActivationFailed(WireModel):
    """Report a failed preparation without changing the active revision."""

    kind: Literal["failed"] = "failed"
    runtime_revision: Identifier
    reason: NonemptyText


class DeactivationRequest(WireModel):
    """Ask a worker to release one runtime revision."""

    runtime_revision: Identifier
    reason: Literal["disable", "replace", "shutdown", "failure"]


class DeactivationResult(WireModel):
    """Report work that still prevents complete worker shutdown."""

    runtime_revision: Identifier
    pending_job_ids: Annotated[tuple[Identifier, ...], Field(max_length=1000)] = ()


type ActivationResult = Annotated[ActivationReady | ActivationFailed, Field(discriminator="kind")]
