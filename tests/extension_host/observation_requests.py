# Copyright (c) 2026 Zhambyl Yermagambet
"""Change complete observation proposals for storage boundary tests."""

from extensions.models.observations import ObservationAppend
from tests.extension_host import lifecycle_fixture as lifecycle, observation_fixture


def document(request: ObservationAppend, encoded: str) -> ObservationAppend:
    """Keep the source key while replacing exact original document bytes.

    Returns:
        A complete request which must pass host validation again.

    """
    positioned = request.observations[0]
    candidate = positioned.observation
    document = candidate.document.model_copy(update={"json_text": encoded})
    changed = candidate.model_copy(update={"document": document})
    positioned = positioned.model_copy(update={"observation": changed})
    return request.model_copy(update={"observations": (positioned,)})


def new_key(request: ObservationAppend, key: str) -> ObservationAppend:
    """Name a second observation from the same source.

    Returns:
        A new input with a distinct key and source position.

    """
    positioned = request.observations[0]
    changed = positioned.observation.model_copy(update={"observation_key": key})
    return request.model_copy(update={"observations": (
        positioned.model_copy(update={"observation": changed, "position": f"position:{key}"}),
    )})


def causes(request: ObservationAppend, parents: tuple[str, ...]) -> ObservationAppend:
    """Set explicit cause references without changing scope.

    Returns:
        A complete proposal with the selected parent references.

    """
    positioned = request.observations[0]
    changed = positioned.observation.model_copy(update={"causes": parents})
    positioned = positioned.model_copy(update={"observation": changed})
    return request.model_copy(update={"observations": (positioned,)})


def reload_request(case: observation_fixture.ObservationCase) -> ObservationAppend:
    """Commit a new runtime while retaining the selected package bytes and settings.

    Returns:
        The same original observation under the new committed runtime.

    """
    state = case.lifecycle.read_extension_lifecycle()
    assert state.committed_runtime is not None
    proposal = lifecycle.proposal(case.lifecycle, state.committed_runtime.packages[0], "next-runtime")
    lifecycle.commit(case.lifecycle, proposal)
    return case.request.model_copy(update={"runtime_revision": proposal.candidate.runtime_revision})
