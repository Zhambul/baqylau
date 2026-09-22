# Copyright (c) 2026 Zhambyl Yermagambet
"""Check exact snapshot binding and complete projection-transform bounds."""

import pytest
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.models.projection_transforms import ProjectionTransformResult
from baqylau_extension_api.models.projections import MAX_PROJECTION_ROWS
from baqylau_extension_api.projection import boundaries
from baqylau_extension_api.projection_transform import results

from tests.extension_api import projection_transform_checks as checks, projection_transform_samples as fixtures

CONTEXT = "context"


@pytest.mark.parametrize(("section", "change"), [
    (CONTEXT, {"runtime_revision": "another"}), (CONTEXT, {"history_revision": "another"}),
    (CONTEXT, {"settings_revision": 1}), (CONTEXT, {"input_cursor": 13}),
    (CONTEXT, {"mode": "replay"}), (CONTEXT, {"extension_id": "test.peer"}),
    ("snapshot", {"projection_generation": "another"}), ("snapshot", {"commit_cursor": 8}),
])
def test_transform_keeps_exact_binding(section: str, change: dict[str, object]) -> None:
    """Reject replies from another runtime, history, snapshot, or settings revision."""
    request = fixtures.request()
    original = request.binding.context if section == CONTEXT else request.binding.snapshot
    binding = request.binding.model_copy(update={section: original.model_copy(update=change)})
    response = ProjectionTransformResult(binding=binding)
    with pytest.raises(ExtensionContractError, match="exact binding"):
        results.validate_transform_reply(request, response)


def test_transform_bounds_full_request_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Count captured aggregates and proposals, not only canonical inputs."""
    request = fixtures.request()
    encoded_size = len(request.model_dump_json().encode("utf-8"))
    monkeypatch.setattr(boundaries, "MAX_PROJECTION_REQUEST_BYTES", encoded_size)
    assert checks.apply(request) == request.changes
    monkeypatch.setattr(boundaries, "MAX_PROJECTION_REQUEST_BYTES", encoded_size - 1)
    with pytest.raises(ExtensionContractError, match="transform request exceeds"):
        checks.apply(request)


def test_transform_bounds_full_reply_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Measure the complete encoded reply, including the binding."""
    request = fixtures.request()
    response = ProjectionTransformResult(binding=request.binding)
    encoded_size = len(response.model_dump_json().encode("utf-8"))
    monkeypatch.setattr(results, "MAX_PROJECTION_RESPONSE_BYTES", encoded_size)
    assert results.validate_transform_reply(request, response) == response
    monkeypatch.setattr(results, "MAX_PROJECTION_RESPONSE_BYTES", encoded_size - 1)
    with pytest.raises(ExtensionContractError, match="result exceeds"):
        results.validate_transform_reply(request, response)


def test_transform_bounds_combined_output_count() -> None:
    """Enforce the result bound after kept inputs and additions are combined."""
    original = fixtures.core_entry()
    changes = tuple(original.model_copy(update={
        "change_id": f"row-{index}", "entry": original.entry.model_copy(update={"entry_id": f"row-{index}"}),
    }) for index in range(MAX_PROJECTION_ROWS))
    request = fixtures.request().model_copy(update={"changes": changes})
    assert len(checks.apply(request)) == MAX_PROJECTION_ROWS
    with pytest.raises(ExtensionContractError, match="output bound"):
        checks.apply(request, checks.core_addition(changes[0]))


def test_transform_rejects_competing_writes() -> None:
    """Distinct change IDs cannot write the same feed row twice."""
    original = fixtures.core_entry()
    duplicate = original.model_copy(update={"change_id": "different-change"})
    request = fixtures.request().model_copy(update={"changes": (original, duplicate)})
    with pytest.raises(ExtensionContractError, match="same target twice"):
        checks.apply(request)
