# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep projection snapshot scope and canonical progress separate."""

import pytest
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.models.projections import ProjectionRequest
from baqylau_extension_api.projection.boundaries import validate_request_boundary

from tests.extension_api import projection_samples, samples

CURSOR = "cursor"


def test_projection_cursor_domains_are_distinct() -> None:
    """Read canonical cursor 12 after 10 while derived data is at commit 7."""
    request = projection_samples.request()
    assert validate_request_boundary(request) == request
    assert request.binding.snapshot.commit_cursor < request.binding.after_input_cursor
    assert ProjectionRequest.model_validate_json(request.model_dump_json()) == request


@pytest.mark.parametrize("change", [{"scope": samples.SESSION}, {"history_revision": "another"}])
def test_projection_rejects_wrong_snapshot(change: dict[str, object]) -> None:
    """Reject a snapshot that does not belong to the processing context."""
    request = projection_samples.request()
    snapshot = request.binding.snapshot.model_copy(update=change)
    binding = request.binding.model_copy(update={"snapshot": snapshot})
    with pytest.raises(ExtensionContractError, match="context scope and history"):
        validate_request_boundary(request.model_copy(update={"binding": binding}))


@pytest.mark.parametrize("cursor", [0, 1, 10, 13])
def test_projection_rejects_fact_outside_batch(cursor: int) -> None:
    """Do not replay old facts in a new batch or accept a fact beyond its boundary."""
    request = projection_samples.request()
    if cursor == 0:
        binding = request.binding.model_copy(update={"after_input_cursor": 13})
        request = request.model_copy(update={"binding": binding})
    else:
        stored = request.events[0].model_copy(update={CURSOR: cursor})
        request = request.model_copy(update={"events": (stored,)})
    with pytest.raises(ExtensionContractError):
        validate_request_boundary(request)


@pytest.mark.parametrize("mode", [CURSOR, "identity", "order"])
def test_projection_rejects_repeated_facts(mode: str) -> None:
    """Reject repeated or out-of-order cursors and repeated fact identities."""
    request = projection_samples.request()
    first = request.events[0]
    second = first
    if mode == "identity":
        first = first.model_copy(update={CURSOR: 11})
    if mode == "order":
        second = first.model_copy(update={
            CURSOR: 11, "fact": first.fact.model_copy(update={"event_id": "event-11"}),
        })
    with pytest.raises(ExtensionContractError):
        validate_request_boundary(request.model_copy(update={
            "events": (first, second),
        }))


def test_projection_rejects_fact_wrong_scope() -> None:
    """Do not mix installation data with an unrelated session fact."""
    request = projection_samples.request()
    fact = request.events[0].fact.model_copy(update={"scope": samples.SESSION})
    stored = request.events[0].model_copy(update={"fact": fact})
    with pytest.raises(ExtensionContractError, match="context scope"):
        validate_request_boundary(request.model_copy(update={"events": (stored,)}))


@pytest.mark.parametrize("change", [
    {"revision": 8},
    {"key": projection_samples.record_key().model_copy(update={"scope": samples.SESSION})},
])
def test_projection_rejects_prior_row_boundary(change: dict[str, object]) -> None:
    """Require every supplied row to exist inside the selected scope and snapshot."""
    request = projection_samples.request()
    row = projection_samples.stored_record().model_copy(update=change)
    with pytest.raises(ExtensionContractError):
        validate_request_boundary(request.model_copy(update={"prior_records": (row,)}))


def test_projection_rejects_duplicate_prior_key() -> None:
    """Reject two competing versions of the same captured record."""
    request = projection_samples.request()
    with pytest.raises(ExtensionContractError, match="prior record keys"):
        validate_request_boundary(request.model_copy(update={"prior_records": request.prior_records * 2}))
