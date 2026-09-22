# Copyright (c) 2026 Zhambyl Yermagambet
"""Require explicit observer policy and declarations before any feature import."""

import pytest
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest.observers import ObserverSelection
from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.manifest.validation import validate_manifest
from pydantic import ValidationError

from tests.extension_api import observer_samples as fixtures


def test_observer_policy_round_trips() -> None:
    """Effect and recovery support remain part of the data-only manifest."""
    manifest = fixtures.manifest(effect="write")
    checked = validate_manifest(manifest)
    assert ExtensionManifest.model_validate_json(checked.model_dump_json()) == checked
    observer = checked.contributions.processing[0]
    assert isinstance(observer, ObserverSelection)
    assert observer.effect == "write"
    assert observer.reconciliation
    assert "ObserverSelection" in ExtensionManifest.model_json_schema()["$defs"]


def test_observer_effect_must_be_explicit() -> None:
    """A package must not acquire write behavior through an omitted default."""
    with pytest.raises(ValidationError):
        ObserverSelection.model_validate({"scopes": ("installation",), "input_types": (fixtures.EVENT_TYPE,)})


@pytest.mark.parametrize(("field", "invalid"), [
    ("effect", "unknown"), ("reconciliation", "true"), ("scopes", ()), ("input_types", ()),
])
def test_observer_policy_fields_are_strict(field: str, invalid: object) -> None:
    """Reject coerced recovery flags and missing or invalid selections."""
    selection = fixtures.manifest().contributions.processing[0]
    with pytest.raises(ValidationError):
        ObserverSelection.model_validate(selection.model_copy(update={field: invalid}))


@pytest.mark.parametrize("change", ["missing", "undeclared", "duplicate"])
def test_observer_requires_one_selection(change: str) -> None:
    """A runtime capability and its trigger declaration must agree."""
    manifest = fixtures.manifest()
    contributions = manifest.contributions
    if change == "missing":
        manifest = manifest.model_copy(update={"contributions": contributions.model_copy(update={
            "processing": contributions.processing[1:],
        })})
    if change == "undeclared":
        manifest = manifest.model_copy(update={"capabilities": tuple(
            name for name in manifest.capabilities if name != "observer"
        )})
    if change == "duplicate":
        manifest = manifest.model_copy(update={"contributions": contributions.model_copy(update={
            "processing": (*contributions.processing, contributions.processing[0]),
        })})
    with pytest.raises(ExtensionContractError):
        validate_manifest(manifest)
