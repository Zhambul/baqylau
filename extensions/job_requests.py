# Copyright (c) 2026 Zhambyl Yermagambet
"""Name one stored job and the requests that stop or reconcile it."""

from dataclasses import dataclass

from baqylau_extension_api.models.documents import Diagnostic, EncodedDocument
from baqylau_extension_api.models.scopes import ExtensionScope

from domain.extension_jobs import JobCancelStatus
from domain.ids import ExtensionJobId


@dataclass(frozen=True)
class JobCancelSubmission:
    """Carry one cancellation request for a stored attempt."""

    expected_revision: int
    reason: str


@dataclass(frozen=True)
class JobReconcileSubmission:
    """Carry one reconciliation request for a stored attempt."""

    expected_revision: int | None = None
    receipt: EncodedDocument | None = None


@dataclass(frozen=True)
class JobCancelOutcome:
    """Keep the checked cancellation state for the API layer."""

    status: JobCancelStatus
    revision: int
    diagnostic: Diagnostic | None = None


@dataclass(frozen=True)
class JobKey:
    """Name one stored job."""

    owner: str
    scope: ExtensionScope
    job_id: ExtensionJobId
