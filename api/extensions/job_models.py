# Copyright (c) 2026 Zhambyl Yermagambet
"""Typed extension job reads."""

from typing import Literal

from baqylau_extension_api.models.command_results import CommandResult
from baqylau_extension_api.models.documents import Diagnostic, EncodedDocument
from baqylau_extension_api.models.observer_results import ObservationJobResult
from pydantic import BaseModel


class ExtensionJobResponse(BaseModel):
    """Describe one stored job and its last result."""

    job_id: str
    kind: Literal["command", "observer"]
    state: Literal["accepted", "running", "succeeded", "failed", "canceled", "outcome_unknown"]
    revision: int
    consumer_cursor: int | None = None
    result: CommandResult | ObservationJobResult | None = None
    diagnostic: Diagnostic | None = None


class ExtensionJobCancelRequest(BaseModel):
    """Request cancellation of one stored job attempt."""

    scope: str
    expected_revision: int
    reason: str


class ExtensionJobCancelResponse(BaseModel):
    """Report a cancellation request without claiming a proven stop."""

    status: Literal["requested", "canceled", "not_running", "outcome_unknown"]
    revision: int
    diagnostic: Diagnostic | None = None


class ExtensionJobReconcileRequest(BaseModel):
    """Inspect an uncertain result without repeating the original command."""

    scope: str
    expected_revision: int | None = None
    receipt: EncodedDocument | None = None
