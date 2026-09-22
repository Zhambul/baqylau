# Copyright (c) 2026 Zhambyl Yermagambet
"""Build complete pure projection boundaries with distinct cursor domains."""

from baqylau_extension_api.manifest import contributions, data, metadata
from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.models import canonical, events, projections, records
from baqylau_extension_api.models.projection_entries import ProjectedEntry
from baqylau_extension_api.models.record_changes import PutRecord
from baqylau_extension_api.models.scopes import SnapshotCursor

from tests.extension_api import manifest_samples, operation_samples, samples, source_samples

COLLECTION = "test.sample.records"
ENTRY_TYPE = "test.sample.card"
EVENT_CURSOR = 12
OCCURRED_AT = 13.0
ACCEPTED_AT = 14.0


def manifest() -> ExtensionManifest:
    """Declare owned feed and record schemas without importing the projector.

    Returns:
        A package with one pure projector and no live capabilities.

    """
    reference = operation_samples.schema_definition().reference
    scopes: data.ScopeKinds = ("session", "workspace", "repository", "installation")
    return manifest_samples.backend_manifest(operation_samples.OWNER).model_copy(update={
        "backend": metadata.BackendEntry(module="projection_backend"),
        "capabilities": ("lifecycle", "projector"), "schemas": (operation_samples.schema_definition(),),
        "contributions": contributions.Contributions(
            event_types=(data.DocumentDefinition(name=source_samples.EVENT_TYPE, schema_ref=reference, scopes=scopes),),
            entry_types=(data.DocumentDefinition(name=ENTRY_TYPE, schema_ref=reference, scopes=scopes),),
            collections=(data.DocumentDefinition(name=COLLECTION, schema_ref=reference, scopes=scopes),),
            processing=(data.ProcessingSelection(
                capability="projector", scopes=scopes,
                input_types=(source_samples.EVENT_TYPE, "session.title_changed"),
            ),),
        ),
    })


def selection_request() -> projections.ProjectionSelectionRequest:
    """Select fact cursor 12 after cursor 10 and projection commit 7.

    Returns:
        A complete captured selection independent of a coding session.

    """
    context = samples.processing_context().model_copy(update={
        "extension_id": operation_samples.OWNER, "scope": source_samples.context().binding.scope,
        "input_cursor": EVENT_CURSOR,
    })
    binding = projections.ProjectionBinding(context=context, after_input_cursor=10, snapshot=SnapshotCursor(
        scope=context.scope, history_revision=context.history_revision,
        projection_generation="projection-1", commit_cursor=7,
    ))
    fact = events.ExtensionFact(
        event_id="event-12", scope=context.scope, event_type=source_samples.EVENT_TYPE,
        document=operation_samples.query_request('"next"').arguments, occurred_at=OCCURRED_AT,
    )
    return projections.ProjectionSelectionRequest(
        binding=binding, events=(canonical.CommittedFact(fact=fact, cursor=EVENT_CURSOR, accepted_at=ACCEPTED_AT),),
    )


def record_key() -> records.RecordKey:
    """Select a stable owned record independently of input fact IDs.

    Returns:
        The fixture's current-state key.

    """
    return records.RecordKey(
        owner=operation_samples.OWNER, collection=COLLECTION,
        scope=source_samples.context().binding.scope, key="current",
    )


def stored_record() -> records.StoredRecord:
    """Capture a row whose revision is before the projection snapshot.

    Returns:
        The exact old record supplied by the host.

    """
    return records.StoredRecord(
        key=record_key(), revision=6, document=operation_samples.query_request('"previous"').arguments,
        summary="Previous state.",
    )


def request() -> projections.ProjectionRequest:
    """Supply selected facts and their captured record state.

    Returns:
        A complete one-fact projection request.

    """
    selected = selection_request()
    return projections.ProjectionRequest(
        binding=selected.binding, events=selected.events, prior_records=(stored_record(),),
    )


def entry(entry_key: str = "summary") -> ProjectedEntry:
    """Use a feature-owned key to create multiple rows from one fact.

    Returns:
        A declared feed row with an immutable source link.

    """
    return ProjectedEntry(
        entry_key=entry_key, source_event_id="event-12", entry_type=ENTRY_TYPE,
        document=operation_samples.query_request('"next"').arguments, summary="Next state.", occurred_at=OCCURRED_AT,
    )


def result() -> projections.ProjectionResult:
    """Propose two feed rows and one replacement in the same result.

    Returns:
        Complete ordered output with no host-assigned commit revision.

    """
    return projections.ProjectionResult(
        binding=request().binding, entries=(entry(), entry("detail")),
        record_changes=(PutRecord(
            key=record_key(), expected_revision=6, document=entry().document, summary=entry().summary,
        ),),
    )
