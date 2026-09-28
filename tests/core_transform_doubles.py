# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide projection transformers that change, add, drop, or fail core feed rows."""

from __future__ import annotations

from dataclasses import dataclass

from baqylau_extension_api.identities import DerivedIdentity, derived_projection_change_id
from baqylau_extension_api.manifest import data
from baqylau_extension_api.models import documents, projection_transforms
from baqylau_extension_api.models.projection_changes import CoreEntryChange, ExtensionEntryChange, ProjectionChange
from baqylau_extension_api.models.projection_entries import ProjectedEntry
from baqylau_extension_api.models.transforms import Drop, Insert, Replace

from tests.projection_pass_fixture import ENTRY_TYPE, MANIFEST, OWNER

CHANGED_SUMMARY = "Changed by an extension"
EXTRA_KEY = "extra"
CORE_TRANSFORM_MANIFEST = MANIFEST.model_copy(update={
    "capabilities": (*MANIFEST.capabilities, "projection_transformer"),
    "contributions": MANIFEST.contributions.model_copy(update={
        "processing": (*MANIFEST.contributions.processing, data.ProcessingSelection(
            capability="projection_transformer", scopes=("session",), input_types=("shell.started",),
        )),
    }),
})


@dataclass(frozen=True)
class SummaryTransformer:
    """Replace the summary of every proposed core feed row."""

    def transform(
        self, transform_request: projection_transforms.ProjectionTransformRequest,
    ) -> projection_transforms.ProjectionTransformResult:
        """Return one replacement per core feed row.

        Returns:
            The complete transform result.

        """
        operations = tuple(
            Replace[ProjectionChange](input_id=change.change_id, document=change.model_copy(update={
                "entry": change.entry.model_copy(update={"summary": CHANGED_SUMMARY}),
            }))
            for change in transform_request.changes
            if isinstance(change, CoreEntryChange)
        )
        return projection_transforms.ProjectionTransformResult(binding=transform_request.binding, operations=operations)


@dataclass(frozen=True)
class ExtraEntryTransformer:
    """Add one owned extension feed row after the first core feed row."""

    def transform(
        self, transform_request: projection_transforms.ProjectionTransformRequest,
    ) -> projection_transforms.ProjectionTransformResult:
        """Return one insertion after the core row.

        Returns:
            The complete transform result.

        """
        anchor = next(change for change in transform_request.changes if isinstance(change, CoreEntryChange))
        change_id = derived_projection_change_id(DerivedIdentity(
            extension_id=OWNER, input_id=anchor.change_id, output_key=EXTRA_KEY,
        ))
        addition = ExtensionEntryChange(
            change_id=change_id,
            owner=OWNER,
            scope=transform_request.binding.context.scope,
            entry=ProjectedEntry(
                entry_key=change_id,
                source_event_id=anchor.source_event_id,
                entry_type=ENTRY_TYPE,
                document=documents.EncodedDocument(schema_ref=MANIFEST.schemas[0].reference, json_text='"extra"'),
                summary="Shell note",
            ),
        )
        return projection_transforms.ProjectionTransformResult(
            binding=transform_request.binding,
            operations=(Insert(input_id=anchor.change_id, output_key=EXTRA_KEY, position="after", document=addition),),
        )


@dataclass(frozen=True)
class DropEntryTransformer:
    """Suppress every proposed core feed row."""

    def transform(
        self, transform_request: projection_transforms.ProjectionTransformRequest,
    ) -> projection_transforms.ProjectionTransformResult:
        """Return one drop per core feed row.

        Returns:
            The complete transform result.

        """
        operations = tuple(
            Drop(input_id=change.change_id, reason="Fixture feed suppression")
            for change in transform_request.changes
            if isinstance(change, CoreEntryChange)
        )
        return projection_transforms.ProjectionTransformResult(binding=transform_request.binding, operations=operations)


@dataclass(frozen=True)
class FailingTransformer:
    """Fail every call."""

    def transform(
        self, transform_request: projection_transforms.ProjectionTransformRequest,
    ) -> projection_transforms.ProjectionTransformResult:
        """Fail before any result.

        Raises:
            RuntimeError: Always.

        """
        input_cursor = transform_request.binding.context.input_cursor
        message = f"transform failed at {input_cursor}"
        raise RuntimeError(message)
