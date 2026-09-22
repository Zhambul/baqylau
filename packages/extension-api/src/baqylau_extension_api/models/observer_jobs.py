# Copyright (c) 2026 Zhambyl Yermagambet
"""Bind live post-commit work to one accepted durable job and its cause."""

from typing import Annotated, Literal

from pydantic import Field

from baqylau_extension_api.models.base import ExtensionId, Identifier, NonemptyText, OpaqueId, Revision, WireModel
from baqylau_extension_api.models.canonical import CommittedFact
from baqylau_extension_api.models.documents import Diagnostic, EncodedDocument
from baqylau_extension_api.models.scopes import ExtensionScope


class ObservationJobBinding(WireModel):
    """Keep the job and cause fixed while each dispatch has its own call ID."""

    extension_id: ExtensionId
    scope: ExtensionScope
    runtime_revision: Identifier
    history_revision: Identifier
    job_id: Identifier
    call_id: Identifier
    event_id: OpaqueId


class ObservationJobRequest(WireModel):
    """Supply one committed trigger; replay cannot dispatch a live observer."""

    binding: ObservationJobBinding
    event: CommittedFact
    deadline: Annotated[float, Field(gt=0)]
    settings_revision: Revision
    settings: EncodedDocument | None = None
    expected_state_revision: Identifier | None = None
    mode: Literal["live"] = "live"


class ObservationCancelRequest(WireModel):
    """Ask the selected observer to stop one accepted attempt."""

    binding: ObservationJobBinding
    reason: NonemptyText


class ObservationCancelResult(WireModel):
    """Do not confuse a stop request with a proven final job outcome."""

    binding: ObservationJobBinding
    status: Literal["requested", "canceled", "not_running", "outcome_unknown"]
    diagnostic: Diagnostic | None = None


class ObservationReconcileRequest(WireModel):
    """Inspect a lost or uncertain result without running observe again."""

    observation: ObservationJobRequest
    receipt: EncodedDocument | None = None
