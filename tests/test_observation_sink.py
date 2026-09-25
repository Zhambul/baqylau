# Copyright (c) 2026 Zhambyl Yermagambet
"""Accept worker observations only from the committed runtime, as its committed manager."""

from dataclasses import dataclass, field

import pytest
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.models import environment, reporting, scopes, source_results

from extensions.models import lifecycle_selection, lifecycle_state, observations
from extensions.observation_sink import HostObservationSink
from tests.extension_api import operation_samples, samples

RUNTIME = samples.RUNTIME_REVISION
MANAGER = "manager-one"
SUBMISSION = reporting.ObservationSubmission(scope=scopes.InstallationScope(), observations=(
    source_results.PositionedObservation(position="one", observation=operation_samples.observation()),
))


@dataclass
class RecordingStores:
    """Name the committed runtime and keep the appends."""

    committed: str = RUNTIME
    appended: list[observations.ObservationAppend] = field(default_factory=list)

    def read_extension_lifecycle(self) -> lifecycle_state.LifecycleState:
        """Name the committed runtime and its manager.

        Returns:
            The fixture lifecycle state.

        """
        runtime = lifecycle_selection.RuntimeSelection(runtime_revision=self.committed, catalog_revision=1, packages=())
        return lifecycle_state.LifecycleState(manager_id=MANAGER, committed_runtime=runtime)

    def append_observations(
        self, observation_append: observations.ObservationAppend,
    ) -> observations.ObservationAppendOutcome:
        """Keep the append.

        Returns:
            No stored rows.

        """
        self.appended.append(observation_append)
        return observations.ObservationAppendOutcome(accepted=(), repeated=())


def sink(stores: RecordingStores) -> HostObservationSink:
    """Bind the sink to the fixture worker and stores.

    Returns:
        The host sink.

    """
    worker = environment.ExtensionEnvironment(extension_info=samples.extension_info(), runtime_revision=RUNTIME)
    return HostObservationSink(worker, stores, stores, clock=lambda: 1.0)  # type: ignore[arg-type]


def test_committed_runtime_submits() -> None:
    """The sink appends as the committed manager and the worker's runtime."""
    stores = RecordingStores()

    submitted = sink(stores).submit_observations(SUBMISSION)

    assert submitted == reporting.ObservationsSubmitted(accepted=0, repeated=0)
    appended = stores.appended[0]
    assert (appended.manager_id, appended.runtime_revision) == (MANAGER, RUNTIME)


def test_candidate_runtime_cannot_submit() -> None:
    """A worker whose runtime is not committed yet cannot store originals."""
    stores = RecordingStores(committed="other-runtime")

    with pytest.raises(ExtensionContractError, match="committed runtime"):
        sink(stores).submit_observations(SUBMISSION)
    assert not stores.appended
