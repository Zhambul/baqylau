# Copyright (c) 2026 Zhambyl Yermagambet
"""Build local projection transforms for the projection pass tests."""

from __future__ import annotations

from dataclasses import dataclass

from baqylau_extension_api.identities import DerivedIdentity, derived_projection_change_id
from baqylau_extension_api.models import documents, projection_transforms
from baqylau_extension_api.models.projection_changes import ExtensionEntryChange, ExtensionRecordChange
from baqylau_extension_api.models.projection_entries import ProjectedEntry
from baqylau_extension_api.models.transforms import Drop, Insert

from tests.projection_pass_fixture import ENTRY_TYPE, EXTRA_KEY, OWNER


@dataclass(frozen=True)
class DropRecordTransformer:
    """Drop every proposed record change from the complete batch."""

    def transform(
        self, transform_request: projection_transforms.ProjectionTransformRequest,
    ) -> projection_transforms.ProjectionTransformResult:
        """Return one drop per record change.

        Returns:
            The complete transform result.

        """
        operations = tuple(
            Drop(input_id=change.change_id, reason="Fixture record suppression")
            for change in transform_request.changes
            if isinstance(change, ExtensionRecordChange)
        )
        return projection_transforms.ProjectionTransformResult(binding=transform_request.binding, operations=operations)


@dataclass(frozen=True)
class AddEntryTransformer:
    """Add one derived entry after the first entry change."""

    def transform(
        self, transform_request: projection_transforms.ProjectionTransformRequest,
    ) -> projection_transforms.ProjectionTransformResult:
        """Return one derived insertion.

        Returns:
            The complete transform result.

        """
        anchor = next(change for change in transform_request.changes if isinstance(change, ExtensionEntryChange))
        change_id = derived_projection_change_id(DerivedIdentity(
            extension_id=OWNER, input_id=anchor.change_id, output_key=EXTRA_KEY,
        ))
        addition = ExtensionEntryChange(
            change_id=change_id,
            owner=OWNER,
            scope=anchor.scope,
            entry=ProjectedEntry(
                entry_key=change_id,
                source_event_id=anchor.entry.source_event_id,
                entry_type=ENTRY_TYPE,
                document=documents.EncodedDocument(
                    schema_ref=anchor.entry.document.schema_ref, json_text='"extra"',
                ),
                summary="Card extra",
                occurred_at=anchor.entry.occurred_at,
            ),
        )
        return projection_transforms.ProjectionTransformResult(
            binding=transform_request.binding,
            operations=(Insert(
                input_id=anchor.change_id, output_key=EXTRA_KEY, position="after", document=addition,
            ),),
        )
