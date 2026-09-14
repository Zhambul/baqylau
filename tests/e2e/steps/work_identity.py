# Copyright (c) 2026 Zhambyl Yermagambet
"""Steps that check work prompt and worker identity."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pytest_bdd import parsers, then

from tests.e2e.testkit.references import WorkerKind, WorkRef, Works
from tests.e2e.testkit.work_workers import worker

if TYPE_CHECKING:
    from api.sessiondata.models.actor import ActorResponse
    from sdk.client import BaqylauClient


@then(parsers.parse('work "{name}" has worker type {worker_type}'))
def work_has_worker_type(
    client: BaqylauClient,
    works: Works,
    name: str,
    worker_type: str,
) -> None:
    """Check one work worker type."""
    work = works[name]
    expected, _ = worker(worker_type)
    actor = client.sessions.snapshot(work.session).actor(work.worker.actor_id)
    assert work.worker.kind == expected
    if expected == WorkerKind.LEAD:
        _assert_lead_work_type(work, actor)
        return
    _assert_subagent_work_type(work, actor)


def _assert_lead_work_type(work: WorkRef, actor: ActorResponse) -> None:
    """Verify the actor and turn identity for lead work."""
    assert actor.parent_actor_id is None
    assert work.assignment is None
    assert (work.turn.actor_id, work.turn.turn_id) == (
        work.request_turn.actor_id,
        work.request_turn.turn_id,
    )


def _assert_subagent_work_type(work: WorkRef, actor: ActorResponse) -> None:
    """Verify the actor and turn identity for subagent work."""
    assert work.worker.parent_actor_id is not None
    assert actor.parent_actor_id == work.worker.parent_actor_id
    assert work.assignment is not None
    assert work.turn.actor_id == actor.actor_id
    assert (work.turn.actor_id, work.turn.turn_id) != (
        work.request_turn.actor_id,
        work.request_turn.turn_id,
    )


@then(parsers.parse("work \"{name}\" has requested prompt '{text}'"))
def work_has_requested_prompt(works: Works, name: str, text: str) -> None:
    """Check one work prompt."""
    assert works[name].requested_prompt == text
