# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep nested execution state and feed origins intact during display changes."""

import pytest
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.models.projection_changes import ProjectionChange
from baqylau_extension_api.models.transforms import Replace

from tests.extension_api import projection_transform_checks as checks, projection_transform_samples as fixtures

ACTOR = fixtures.actor_change().actor
ACTOR_FIELD = "actor"
OTHER_ID = "another"


@pytest.mark.parametrize("change", [
    {"background": ACTOR.background.model_copy(update={"running_shell_ids": ()})},
    {"background": ACTOR.background.model_copy(update={"monitor_count": 0})},
    {"background": ACTOR.background.model_copy(update={"background_job_count": 0})},
    {"context": ACTOR.context.model_copy(update={"compacting": not ACTOR.context.compacting})},
    {"statistics": ACTOR.statistics.model_copy(update={"active_since_internal": None})},
    {"statistics": ACTOR.statistics.model_copy(update={"file_paths_internal": ()})},
])
def test_transform_keeps_nested_execution_state(change: dict[str, object]) -> None:
    """Reject changes that would lose active work or alter later calculations."""
    original = fixtures.actor_change()
    replacement = original.model_copy(update={ACTOR_FIELD: ACTOR.model_copy(update=change)})
    with pytest.raises(ExtensionContractError, match="actor execution state"):
        checks.apply(fixtures.request(), Replace[ProjectionChange](
            input_id=original.change_id, document=replacement,
        ))


def test_transform_can_change_visible_totals() -> None:
    """Permit display totals while retaining their internal calculation inputs."""
    original = fixtures.actor_change()
    actor = ACTOR.model_copy(update={
        "statistics": ACTOR.statistics.model_copy(update={"prompt_count": 100}),
        "context": ACTOR.context.model_copy(update={"used_tokens": 123}),
    })
    replacement = original.model_copy(update={ACTOR_FIELD: actor})
    output = checks.apply(fixtures.request(), Replace[ProjectionChange](
        input_id=original.change_id, document=replacement,
    ))
    assert output[1] == replacement
    assert actor.statistics.file_paths_internal == ACTOR.statistics.file_paths_internal


@pytest.mark.parametrize("change", [
    {"entry_id": OTHER_ID}, {"actor_id": OTHER_ID}, {"parent_actor_id": OTHER_ID},
    {"turn_id": OTHER_ID}, {"session_id": OTHER_ID},
])
def test_transform_keeps_core_feed_source(change: dict[str, object]) -> None:
    """A feed replacement cannot change the row identity or source metadata."""
    original = fixtures.core_entry()
    replacement = original.model_copy(update={"entry": original.entry.model_copy(update=change)})
    with pytest.raises(ExtensionContractError, match="core feed identity"):
        checks.apply(fixtures.request(), Replace[ProjectionChange](
            input_id=original.change_id, document=replacement,
        ))


def test_transform_cannot_create_core_actor() -> None:
    """Use canonical lifecycle processing to create an actor, not display insertion."""
    original = fixtures.core_entry()
    addition = checks.extension_addition(original)
    actor = ACTOR.model_copy(update={"actor_id": "new-actor"})
    document = fixtures.actor_change().model_copy(update={
        "change_id": addition.document.change_id, ACTOR_FIELD: actor,
    })
    with pytest.raises(ExtensionContractError, match="actor execution state"):
        checks.apply(fixtures.request(), addition.model_copy(update={"document": document}))


def test_transform_can_insert_existing_actor_edit() -> None:
    """Add a display update for an existing actor that has no prior write proposal."""
    original = fixtures.core_entry()
    request = fixtures.request().model_copy(update={"changes": (original,)})
    addition = checks.extension_addition(original)
    document = fixtures.actor_change().model_copy(update={"change_id": addition.document.change_id})
    output = checks.apply(request, addition.model_copy(update={"document": document}))
    assert output == (original, document)


def test_transform_rejects_combined_actor_cycle() -> None:
    """Reject a cycle that uses both a captured actor and a proposed actor row."""
    request = fixtures.request()
    parent = ACTOR.model_copy(update={"parent_actor_id": "new-child"})
    child = ACTOR.model_copy(update={"actor_id": "new-child", "parent_actor_id": ACTOR.actor_id})
    before = request.before_core.model_copy(update={"actors": (parent,)})
    proposal = fixtures.actor_change().model_copy(update={ACTOR_FIELD: child})
    with pytest.raises(ExtensionContractError, match="contain a cycle"):
        checks.apply(request.model_copy(update={"before_core": before, "changes": (proposal,)}))
