# Copyright (c) 2026 Zhambyl Yermagambet
"""Describe accepted jobs, cancellation requests, and result reconciliation."""

from typing import Literal

from baqylau_extension_api.models.base import Identifier, NonemptyText, Revision, WireModel
from baqylau_extension_api.models.documents import Diagnostic, EncodedDocument
from baqylau_extension_api.models.operations import OperationBinding


class CommandBinding(OperationBinding):
    """Identify one dispatch attempt of an accepted durable job.

    The call ID changes for a new dispatch or reconciliation attempt. The job
    ID and request key remain fixed. A reply must repeat the complete binding.
    """

    job_id: Identifier
    request_key: Identifier


class CommandRequest(WireModel):
    """Run one accepted job with captured settings and an explicit state check."""

    binding: CommandBinding
    arguments: EncodedDocument
    settings_revision: Revision
    settings: EncodedDocument | None = None
    expected_state_revision: Identifier | None = None


class CommandCancelRequest(WireModel):
    """Request cancellation of the identified execution attempt."""

    binding: CommandBinding
    reason: NonemptyText


class CommandCancelResult(WireModel):
    """Distinguish requested cancellation from a proven stopped operation."""

    binding: CommandBinding
    status: Literal["requested", "canceled", "not_running", "outcome_unknown"]
    diagnostic: Diagnostic | None = None


class CommandReconcileRequest(WireModel):
    """Inspect an uncertain result without repeating the original write."""

    command: CommandRequest
    receipt: EncodedDocument | None = None
