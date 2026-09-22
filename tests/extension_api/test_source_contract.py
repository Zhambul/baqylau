# Copyright (c) 2026 Zhambyl Yermagambet
"""Check complete source plans before registering watches or live reads."""

import pytest
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest.validation import validate_manifest
from baqylau_extension_api.models.source_results import SourcePlan, SourceReleaseResult
from baqylau_extension_api.models.sources import SourceDescriptor, SourceReadRequest, SourceReleaseRequest
from baqylau_extension_api.schemas import SchemaSet
from baqylau_extension_api.sources import plans, registration
from pydantic import ValidationError

from tests.extension_api import samples, source_samples


def test_source_plan_uses_declared_scope() -> None:
    """Accept a complete source plan with no session or live poll requirement."""
    manifest = validate_manifest(source_samples.manifest())
    request = source_samples.context()
    response = SourcePlan(binding=request.binding, sources=(source_samples.descriptor(),))
    assert registration.validate_source_context(manifest, SchemaSet(manifest.schemas), request) == request
    assert plans.validate_source_plan(request, response) == response
    plans.validate_plan_documents(manifest, SchemaSet(manifest.schemas), response)


@pytest.mark.parametrize("change", [
    {"extension_id": "peer"}, {"call_id": "late"}, {"runtime_revision": "old"},
])
def test_source_plan_rejects_stale_call(change: dict[str, str]) -> None:
    """Reject a complete-looking plan from another owner, call, or runtime."""
    request = source_samples.context()
    response = SourcePlan(binding=request.binding.model_copy(update=change))
    with pytest.raises(ExtensionContractError, match="requested call"):
        plans.validate_source_plan(request, response)


def test_source_plan_rejects_duplicate_identities() -> None:
    """Do not attach conflicting readers under the same source identity."""
    request = source_samples.context()
    repeated = (source_samples.descriptor(), source_samples.descriptor())
    response = SourcePlan(binding=request.binding, sources=repeated)
    with pytest.raises(ExtensionContractError, match="source identities"):
        plans.validate_source_plan(request, response)


@pytest.mark.parametrize("change", [
    {"source_type": "peer.source"}, {"state": samples.encoded_document()},
    {"watch_paths": ("/workspace/repeated", "/workspace/repeated")},
])
def test_source_descriptor_checks_ownership(change: dict[str, object]) -> None:
    """Reject undeclared source types, unowned state, and repeated watches."""
    manifest = source_samples.manifest()
    source = source_samples.descriptor().model_copy(update=change)
    with pytest.raises(ExtensionContractError):
        registration.validate_source_descriptor(
            manifest, SchemaSet(manifest.schemas), source_samples.context().binding, source,
        )


@pytest.mark.parametrize("change", [
    {"watch_paths": ("../escaped",)}, {"watch_paths": ("/workspace/../escaped",)}, {"next_due_at": -1.0},
    {"next_due_at": float("inf")}, {"next_due_at": float("nan")},
])
def test_source_descriptor_rejects_invalid_fields(change: dict[str, object]) -> None:
    """Keep paths normalized and deadlines finite and nonnegative."""
    with pytest.raises(ValidationError):
        SourceDescriptor.model_validate({**source_samples.descriptor().model_dump(), **change})


@pytest.mark.parametrize("limit", [0, -1, 257, "10", True])
def test_source_read_limit_is_strict(limit: object) -> None:
    """Keep source reads bounded without coercing strings or booleans."""
    with pytest.raises(ValidationError):
        SourceReadRequest.model_validate({**source_samples.read_request().model_dump(), "limit": limit})


def test_source_release_requires_exact_selection() -> None:
    """A pending release does not clear a different source or scope."""
    request = SourceReleaseRequest(binding=source_samples.context().binding, source_identity="one", reason="Disable")
    response = SourceReleaseResult(binding=request.binding, source_identity="one", status="pending")
    assert plans.validate_source_release(request, response).status == "pending"
    with pytest.raises(ExtensionContractError):
        plans.validate_source_release(request, response.model_copy(update={"source_identity": "other"}))
