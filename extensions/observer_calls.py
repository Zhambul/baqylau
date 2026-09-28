# Copyright (c) 2026 Zhambyl Yermagambet
"""Build the checked request of one observer call and check its reply."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import uuid4

from baqylau_extension_api.models.canonical import CommittedFact
from baqylau_extension_api.models.observer_jobs import ObservationJobBinding, ObservationJobRequest
from baqylau_extension_api.models.scopes import ExtensionScope
from baqylau_extension_api.observers import requests as observer_requests, results as observer_results
from baqylau_extension_api.observers.registration import observer_selection

if TYPE_CHECKING:
    from baqylau_extension_api.models.observer_results import ObservationJobResult

    from extensions.observer_packages import ObserverPackage

OBSERVER_DEADLINE_SECONDS = 30.0


@dataclass(frozen=True)
class ObservationTarget:
    """Name the job, the trigger, and the history of one observer request."""

    scope: ExtensionScope
    history_revision: str
    job_id: str
    trigger: CommittedFact


def observation_request(package: ObserverPackage, target: ObservationTarget) -> ObservationJobRequest:
    """Build and check one observer request for the package's current runtime and settings.

    A write observer gets the committed boundary that it saw as its expected
    state revision.

    Returns:
        The checked request with a new call identity.

    """
    binding = ObservationJobBinding(
        extension_id=package.extension_id,
        scope=target.scope,
        runtime_revision=package.runtime_revision,
        history_revision=target.history_revision,
        job_id=target.job_id,
        call_id=uuid4().hex,
        event_id=target.trigger.fact.event_id,
    )
    write = observer_selection(package.manifest, binding).effect == "write"
    trigger = target.trigger
    request = ObservationJobRequest(
        binding=binding,
        event=trigger,
        deadline=OBSERVER_DEADLINE_SECONDS,
        settings_revision=package.settings.revision,
        settings=package.settings.for_scope(target.scope),
        expected_state_revision=f"{target.history_revision}:{trigger.cursor}" if write else None,
    )
    return observer_requests.validate_observation_request(package.manifest, package.schemas, request)


def is_current(package: ObserverPackage, request: ObservationJobRequest) -> bool:
    """Tell whether a stored request still matches the package's runtime and settings.

    Returns:
        True when the runtime and the settings revision are unchanged.

    """
    return (request.binding.runtime_revision, request.settings_revision) == (
        package.runtime_revision, package.settings.revision,
    )


def checked_result(
    package: ObserverPackage, binding: ObservationJobBinding, reply: ObservationJobResult,
) -> ObservationJobResult:
    """Check one observer reply against the attempt's binding and the package schemas.

    Returns:
        The checked result.

    """
    checked = observer_results.validate_observation_result(binding, reply)
    observer_results.validate_observation_documents(package.manifest, package.schemas, checked)
    return checked
