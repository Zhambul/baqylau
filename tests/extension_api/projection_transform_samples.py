# Copyright (c) 2026 Zhambyl Yermagambet
"""Build complete core and extension proposals for projection transforms."""

from baqylau_extension_api.manifest import data, metadata
from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.models import projection_changes as changes
from baqylau_extension_api.models.projection_transforms import ProjectionTransformRequest
from baqylau_extension_api.models.scopes import SessionScope

from extensions.mapper import core_entries
from tests.extension_api import core_projection_samples, operation_samples, projection_samples, transform_samples

SCOPE = SessionScope(
    session_id=core_projection_samples.AGGREGATE.actors[0].session_id,
    actor_id=core_projection_samples.AGGREGATE.actors[0].actor_id, harness="test",
)
SCOPE_FIELD = "scope"


def manifest() -> ExtensionManifest:
    """Declare a pure projection transformer with owned feed and record schemas.

    Returns:
        A data-only declaration for the external transform fixture.

    """
    base = projection_samples.manifest()
    return base.model_copy(update={
        "backend": metadata.BackendEntry(module="projection_transform_backend"),
        "capabilities": ("lifecycle", "projection_transformer"),
        "contributions": base.contributions.model_copy(update={"processing": (data.ProcessingSelection(
            capability="projection_transformer", scopes=("session", "installation", "repository", "workspace"),
            input_types=base.contributions.processing[0].input_types,
        ),)}),
    })


def request() -> ProjectionTransformRequest:
    """Capture one fact, a complete core aggregate, and an owned record.

    Returns:
        Five ordered proposed writes at an exact projection snapshot.

    """
    base = projection_samples.request()
    binding = base.binding.model_copy(update={
        "context": base.binding.context.model_copy(update={SCOPE_FIELD: SCOPE}),
        "snapshot": base.binding.snapshot.model_copy(update={SCOPE_FIELD: SCOPE}),
    })
    fact = transform_samples.core_fact("event-12").model_copy(update={SCOPE_FIELD: SCOPE})
    prior = projection_samples.stored_record().model_copy(update={"key": record_change().write.key})
    return ProjectionTransformRequest(
        binding=binding, events=(base.events[0].model_copy(update={"fact": fact}),),
        before_core=core_projection_samples.AGGREGATE, prior_records=(prior,),
        changes=(session_change(), actor_change(), core_entry(), extension_entry(), record_change()),
    )


def session_change() -> changes.CoreSessionChange:
    """Propose a visible title change without changing lifecycle state.

    Returns:
        A complete typed session update.

    """
    session = core_projection_samples.AGGREGATE.session
    assert session is not None
    return changes.CoreSessionChange(
        change_id="session-change", session=session.model_copy(update={"title": "Proposed"}),
    )


def actor_change() -> changes.CoreActorChange:
    """Propose a visible actor name change while keeping active work.

    Returns:
        A complete typed actor update.

    """
    actor = core_projection_samples.AGGREGATE.actors[0]
    return changes.CoreActorChange(change_id="actor-change", actor=actor.model_copy(update={"name": "Proposed"}))


def core_entry() -> changes.CoreEntryChange:
    """Build a core feed proposal that keeps the captured fact's source fields.

    Returns:
        One typed message entry with no host commit cursor.

    """
    original = core_projection_samples.original_entry(core_projection_samples.BODIES[2])
    entry = core_entries.public_entry(original).model_copy(update={
        "entry_id": "event-12", "parent_actor_id": None, "turn_id": None,
    })
    return changes.CoreEntryChange(change_id="core-entry", source_event_id="event-12", entry=entry)


def extension_entry() -> changes.ExtensionEntryChange:
    """Build an owned extension feed row for the same canonical cause.

    Returns:
        A declared extension entry in the current session scope.

    """
    return changes.ExtensionEntryChange(
        change_id="extension-entry", owner=operation_samples.OWNER, scope=SCOPE, entry=projection_samples.entry(),
    )


def record_change() -> changes.ExtensionRecordChange:
    """Propose replacement of a captured extension record.

    Returns:
        A typed write with the original expected record revision.

    """
    write = projection_samples.result().record_changes[0]
    return changes.ExtensionRecordChange(change_id="record-change", write=write.model_copy(update={
        "key": write.key.model_copy(update={SCOPE_FIELD: SCOPE}),
    }))
