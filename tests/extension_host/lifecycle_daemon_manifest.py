# Copyright (c) 2026 Zhambyl Yermagambet
"""Declare a core-aware canonical package for the private daemon acceptance."""

from pathlib import Path

from baqylau_extension_api.manifest import contributions, data
from baqylau_extension_api.manifest.metadata import BackendEntry
from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.models import documents

from tests.extension_api import lifecycle_canonical_example as example, manifest_samples, source_samples

BACKEND_NAME = "lifecycle_backend"
RAW_INPUT_TYPES = ("hook",)
CANONICAL_INPUT_TYPES = (
    "turn.started",
    "turn.finished",
    "turn.aborted",
    "message.created",
    "message.queued",
    "reasoning.created",
    "session.title_changed",
    "actor.finished",
)


def backend_source() -> bytes:
    """Read the self-contained backend module the package will run.

    Returns:
        The exact backend module bytes.

    """
    assert example.__file__ is not None
    return Path(example.__file__).read_bytes()


def manifest(owner: str, backend: BackendEntry) -> ExtensionManifest:
    """Declare the canonical transformer and the added fact type for one owner.

    Returns:
        A complete data-only manifest with the fixture's text schema.

    """
    schema = documents.SchemaDefinition(reference=example.schema_reference(owner), json_text=example.SCHEMA_TEXT)
    selections: tuple[data.ProcessingSelection, ...] = (data.ProcessingSelection(
        capability="canonical_transformer", scopes=source_samples.SCOPES, input_types=CANONICAL_INPUT_TYPES,
    ),)
    capabilities = ["lifecycle", "canonical_transformer"]
    if example.behavior_of(owner).startswith(example.RAW_BEHAVIOR_PREFIX):
        capabilities.append("raw_transformer")
        selections += (data.ProcessingSelection(
            capability="raw_transformer", scopes=source_samples.SCOPES, input_types=RAW_INPUT_TYPES,
        ),)
    return manifest_samples.backend_manifest(owner).model_copy(update={
        "backend": backend.model_copy(update={"module": BACKEND_NAME}),
        "capabilities": tuple(capabilities),
        "schemas": (schema,),
        "contributions": contributions.Contributions(
            event_types=(
                data.DocumentDefinition(
                    name=f"{owner}{example.EVENT_SUFFIX}", schema_ref=schema.reference, scopes=source_samples.SCOPES,
                ),
            ),
            processing=selections,
        ),
    })
