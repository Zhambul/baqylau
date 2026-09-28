# Copyright (c) 2026 Zhambyl Yermagambet
"""Describe peer command jobs: the request, the job read, and the stop request."""

from typing import Annotated, Literal

from pydantic import Field

from baqylau_extension_api.models.base import Identifier, WireModel
from baqylau_extension_api.models.command_results import CommandResult
from baqylau_extension_api.models.documents import Diagnostic, EncodedDocument
from baqylau_extension_api.models.services import (
    ServiceBinding,
    ServiceResolveRequest,
    ServiceRevision,
    ServiceUnavailable,
)

MAX_PEER_REQUEST_KEY_LENGTH = 64

type PeerJobState = Literal["accepted", "running", "succeeded", "failed", "canceled", "outcome_unknown"]


class ServiceCommandRequest(ServiceResolveRequest):
    """Ask the host to accept one public peer command as a durable job.

    The host never calls the peer's command directly. It applies the same
    declaration, scope, read-only, deduplication, and state checks as a
    frontend command, and returns a job reference.
    """

    service_revision: ServiceRevision
    command_id: Identifier
    arguments: EncodedDocument
    request_key: Annotated[Identifier, Field(max_length=MAX_PEER_REQUEST_KEY_LENGTH)]
    expected_state_revision: Identifier | None = None


class ServiceJobRequest(ServiceResolveRequest):
    """Read one peer job that this caller submitted."""

    job_id: Identifier


class ServiceJobCancelRequest(ServiceJobRequest):
    """Ask the owning peer to stop one attempt that this caller submitted."""

    expected_revision: Annotated[int, Field(ge=1)]
    reason: Annotated[str, Field(min_length=1, max_length=1000)]


class ServiceJob(WireModel):
    """Report one stored peer job without its private request document."""

    status: Literal["available"] = "available"
    binding: ServiceBinding
    job_id: Identifier
    state: PeerJobState
    revision: Annotated[int, Field(ge=1)]
    result: CommandResult | None = None
    diagnostic: Diagnostic | None = None


class ServiceJobCancelled(WireModel):
    """Report a stop request without claiming a proven final state."""

    status: Literal["available"] = "available"
    binding: ServiceBinding
    job_id: Identifier
    cancel_status: Literal["requested", "canceled", "not_running", "outcome_unknown"]
    revision: Annotated[int, Field(ge=1)]


type ServiceJobResult = Annotated[ServiceJob | ServiceUnavailable, Field(discriminator="status")]
type ServiceJobCancelResult = Annotated[ServiceJobCancelled | ServiceUnavailable, Field(discriminator="status")]
