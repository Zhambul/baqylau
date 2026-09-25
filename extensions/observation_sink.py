# Copyright (c) 2026 Zhambyl Yermagambet
"""Append one worker's new originals under the committed runtime."""

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from baqylau_extension_api.contracts.reporting import ExtensionObservationSink
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.models import reporting
from baqylau_extension_api.models.environment import ExtensionEnvironment

from extensions.models.observations import ObservationAppend
from repository.contract.extension_lifecycle import ExtensionLifecycleRepository
from repository.contract.observations import ObservationRepository


@dataclass(frozen=True)
class HostObservationSink(ExtensionObservationSink):
    """Store a worker's originals only while its runtime is the committed one."""

    environment: ExtensionEnvironment
    observations: ObservationRepository
    lifecycle: ExtensionLifecycleRepository
    clock: Callable[[], float] = field(default=time.time)

    def submit_observations(
        self, observation_submission: reporting.ObservationSubmission,
    ) -> reporting.ObservationsSubmitted:
        """Validate and append the originals as the worker's committed runtime.

        Returns:
            The new and repeated counts.

        """
        checked = reporting.ObservationSubmission.model_validate(observation_submission)
        runtime = self.environment.runtime_revision
        outcome = self.observations.append_observations(ObservationAppend(
            extension_id=self.environment.extension_info.extension_id,
            manager_id=_committed_manager(self.lifecycle, runtime),
            runtime_revision=runtime, scope=checked.scope, observed_at=self.clock(),
            observations=checked.observations,
        ))
        return reporting.ObservationsSubmitted(accepted=len(outcome.accepted), repeated=len(outcome.repeated))


def _committed_manager(lifecycle: ExtensionLifecycleRepository, runtime_revision: str) -> str:
    """Name the manager of the committed runtime, which must be the worker's runtime.

    Returns:
        The committed manager.

    Raises:
        ExtensionContractError: If the worker's runtime is not the committed runtime.

    """
    state = lifecycle.read_extension_lifecycle()
    committed = state.committed_runtime
    if state.manager_id is None or committed is None or committed.runtime_revision != runtime_revision:
        message = "only a committed runtime can submit observations"
        raise ExtensionContractError(message)
    return state.manager_id
