# Copyright (c) 2026 Zhambyl Yermagambet
"""Require complete ordered feed output tied to one immutable projection call."""

import pytest
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.models.projections import ProjectionResult

from tests.extension_api import operation_samples, projection_checks, projection_samples


def test_projection_keeps_two_entries_in_order() -> None:
    """Produce two display rows from one immutable fact and one record replacement."""
    request = projection_samples.request()
    response = projection_samples.result()
    assert projection_checks.validate_result(request, response) == response
    assert tuple(entry.entry_key for entry in response.entries) == ("summary", "detail")
    assert ProjectionResult.model_validate_json(response.model_dump_json()) == response
    assert request == projection_samples.request()


@pytest.mark.parametrize("change", [
    {"runtime_revision": "runtime-2"}, {"input_cursor": 13}, {"settings_revision": 1},
    {"history_revision": "history-2"}, {"mode": "replay"},
])
def test_projection_rejects_changed_context(change: dict[str, object]) -> None:
    """A result cannot move to another attempt, input, settings, or history."""
    response = projection_samples.result()
    context = response.binding.context.model_copy(update=change)
    response = response.model_copy(update={
        "binding": response.binding.model_copy(update={"context": context}),
    })
    with pytest.raises(ExtensionContractError, match="exact processing and snapshot"):
        projection_checks.validate_result(projection_samples.request(), response)


@pytest.mark.parametrize("change", [{"projection_generation": "projection-2"}, {"commit_cursor": 8}])
def test_projection_rejects_changed_snapshot(change: dict[str, object]) -> None:
    """Do not apply old projection output to a newer record snapshot."""
    response = projection_samples.result()
    snapshot = response.binding.snapshot.model_copy(update=change)
    response = response.model_copy(update={
        "binding": response.binding.model_copy(update={"snapshot": snapshot}),
    })
    with pytest.raises(ExtensionContractError, match="exact processing and snapshot"):
        projection_checks.validate_result(projection_samples.request(), response)


@pytest.mark.parametrize("change", [
    {"source_event_id": "unknown"}, {"entry_type": "test.sample.unknown"},
    {"document": operation_samples.query_request("123").arguments},
])
def test_projection_rejects_invalid_entry(change: dict[str, object]) -> None:
    """Reject an unknown cause, unregistered feed type, or invalid row document."""
    response = projection_samples.result()
    response = response.model_copy(update={
        "entries": (response.entries[0].model_copy(update=change),),
    })
    with pytest.raises(ExtensionContractError):
        projection_checks.validate_result(projection_samples.request(), response)


def test_projection_rejects_duplicate_entry_key() -> None:
    """Require each row key to be distinct for the same source fact."""
    response = projection_samples.result().model_copy(update={
        "entries": (projection_samples.entry(),) * 2,
    })
    with pytest.raises(ExtensionContractError, match="unique per source event"):
        projection_checks.validate_result(projection_samples.request(), response)


def test_empty_projection_keeps_input_boundary() -> None:
    """Return no changes without losing the boundary the host must advance."""
    request = projection_samples.request().model_copy(update={"events": (), "prior_records": ()})
    response = ProjectionResult(binding=request.binding)
    assert projection_checks.validate_result(request, response) == response
    assert response.binding.context.input_cursor == projection_samples.EVENT_CURSOR


def test_entry_keys_can_repeat_for_distinct_facts() -> None:
    """The immutable cause is part of a row's logical identity."""
    request = projection_samples.request()
    first = request.events[0].model_copy(update={
        "cursor": 11, "fact": request.events[0].fact.model_copy(update={"event_id": "event-11"}),
    })
    request = request.model_copy(update={"events": (first, *request.events)})
    response = projection_samples.result().model_copy(update={"entries": (
        projection_samples.entry().model_copy(update={"source_event_id": "event-11"}), projection_samples.entry(),
    )})
    assert projection_checks.validate_result(request, response) == response
