# Copyright (c) 2026 Zhambyl Yermagambet
"""Allow visible core changes while retaining required execution state."""

import pytest
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.models.projection_changes import ProjectionChange
from baqylau_extension_api.models.transforms import Drop, Replace

from tests.extension_api import projection_transform_checks as checks, projection_transform_samples as fixtures

SESSION_FIELD = "session"
ACTOR_FIELD = "actor"
STATE_FIELD = "state"
FINISHED = "finished"


def test_projection_transform_changes_core_title() -> None:
    """Change a proposed title without losing any hidden session state."""
    original = fixtures.session_change()
    replacement = original.model_copy(update={SESSION_FIELD: original.session.model_copy(update={
        "title": "Changed",
    })})
    output = checks.apply(fixtures.request(), Replace[ProjectionChange](
        input_id=original.change_id, document=replacement,
    ))
    assert output[0] == replacement
    assert fixtures.request().before_core.session != replacement.session


def test_projection_transform_changes_actor_name() -> None:
    """Change the visible name without clearing active shells or attention state."""
    original = fixtures.actor_change()
    replacement = original.model_copy(update={ACTOR_FIELD: original.actor.model_copy(update={
        "name": "Changed",
    })})
    output = checks.apply(fixtures.request(), Replace[ProjectionChange](
        input_id=original.change_id, document=replacement,
    ))
    assert output[1] == replacement
    assert replacement.actor.background == original.actor.background


@pytest.mark.parametrize("change", [
    {STATE_FIELD: FINISHED}, {"working_directory": "/different"}, {"lead_actor_id": "another"},
    {"custom_title_internal": "hidden"}, {"task_order_internal": ()}, {"continued_from": None},
])
def test_projection_rejects_session_state_change(change: dict[str, object]) -> None:
    """Require canonical processing for lifecycle, identity, and calculation changes."""
    original = fixtures.session_change()
    replacement = original.model_copy(update={SESSION_FIELD: original.session.model_copy(update=change)})
    with pytest.raises(ExtensionContractError, match="session execution state"):
        checks.apply(fixtures.request(), Replace[ProjectionChange](
            input_id=original.change_id, document=replacement,
        ))


@pytest.mark.parametrize("change", [
    {STATE_FIELD: FINISHED}, {"role": "child"}, {"parent_actor_id": "another"},
    {"pending_attention_internal": ()}, {"running_assignment_ids_internal": ()},
])
def test_projection_rejects_actor_state_change(change: dict[str, object]) -> None:
    """Protect actor lifecycle and live-work references from display changes."""
    original = fixtures.actor_change()
    replacement = original.model_copy(update={ACTOR_FIELD: original.actor.model_copy(update=change)})
    with pytest.raises(ExtensionContractError, match="actor execution state"):
        checks.apply(fixtures.request(), Replace[ProjectionChange](
            input_id=original.change_id, document=replacement,
        ))


def test_display_only_core_drop_is_allowed() -> None:
    """Suppress proposed title and name changes when required state stays intact."""
    request = fixtures.request()
    output = checks.apply(request, Drop(input_id="session-change", reason="Keep title"), Drop(
        input_id="actor-change", reason="Keep name",
    ))
    assert output == request.changes[2:]


@pytest.mark.parametrize("kind", [SESSION_FIELD, ACTOR_FIELD])
def test_core_lifecycle_drop_is_rejected(kind: str) -> None:
    """Do not suppress a required finish transition with a display-only drop."""
    request = fixtures.request()
    if kind == SESSION_FIELD:
        original = fixtures.session_change()
        changed: ProjectionChange = original.model_copy(update={SESSION_FIELD: original.session.model_copy(update={
            STATE_FIELD: FINISHED, "finished_at": 20.0,
        })})
    else:
        actor = fixtures.actor_change()
        changed = actor.model_copy(update={ACTOR_FIELD: actor.actor.model_copy(update={
            STATE_FIELD: FINISHED,
        })})
    request = request.model_copy(update={"changes": (changed,)})
    with pytest.raises(ExtensionContractError, match="execution state"):
        checks.apply(request, Drop(input_id=changed.change_id, reason="Hide finished row"))
