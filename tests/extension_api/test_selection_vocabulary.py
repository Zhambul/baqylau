# Copyright (c) 2026 Zhambyl Yermagambet
"""A fact selection names only input types that exist, alone and in an active set (P01-T04)."""

import pytest
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest.activation import activation_order
from baqylau_extension_api.manifest.data import DocumentDefinition, ProcessingSelection
from baqylau_extension_api.manifest.metadata import PackageDependency
from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.manifest.validation import validate_manifest
from baqylau_extension_api.models.documents import SchemaRef

from tests.extension_api import operation_samples, projection_samples

PEER = "test.peer"


def selecting(*input_types: str, dependency: bool = False) -> ExtensionManifest:
    """Give the projector sample a selection of these input types.

    Returns:
        The manifest, before validation.

    """
    manifest = projection_samples.manifest()
    selection = ProcessingSelection(capability="projector", scopes=("session",), input_types=input_types)
    contributions = manifest.contributions.model_copy(update={"processing": (selection,)})
    peers = (PackageDependency(extension_id=PEER, version_range=">=1.0.0", required=False),) if dependency else ()
    return manifest.model_copy(update={"contributions": contributions, "dependencies": peers})


def peer(*event_types: str) -> ExtensionManifest:
    """Give a peer package that declares these event types.

    Returns:
        The manifest.

    """
    base = operation_samples.manifest()
    schema = base.schemas[0].reference.model_copy(update={"owner": PEER})
    definitions = tuple(_declared(name, schema) for name in event_types)
    contributions = base.contributions.model_copy(update={
        "event_types": definitions, "queries": (), "commands": (), "processing": (),
    })
    return base.model_copy(update={
        "extension_id": PEER, "schemas": (base.schemas[0].model_copy(update={"reference": schema}),),
        "contributions": contributions, "capabilities": ("lifecycle",),
    })


def _declared(name: str, schema: SchemaRef) -> DocumentDefinition:
    return DocumentDefinition(name=name, schema_ref=schema, scopes=("session",))


def test_possible_types_are_accepted() -> None:
    """A core kind, an owned event type, and a type in another package's namespace are accepted."""
    owned = projection_samples.manifest().contributions.event_types[0].name

    manifest = validate_manifest(selecting("shell.finished", owned, f"{PEER}.ran", dependency=True))

    selected = manifest.contributions.processing[0]
    assert selected.input_types[0] == "shell.finished"


@pytest.mark.parametrize("input_type", ["shell.finsihed", "shell_started", f"{operation_samples.OWNER}.missing"])
def test_impossible_types_are_refused(input_type: str) -> None:
    """A misspelled core kind, a feed entry kind, and an undeclared owned type are refused."""
    with pytest.raises(ExtensionContractError, match="cannot exist"):
        validate_manifest(selecting(input_type))


def test_present_dependency_must_declare_the_type() -> None:
    """With the dependency in the active set, its type must be one that it declares."""
    with pytest.raises(ExtensionContractError, match="does not declare the selected input type"):
        activation_order((selecting(f"{PEER}.ran", dependency=True), peer(f"{PEER}.other")))
