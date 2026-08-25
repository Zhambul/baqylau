"""Named worktree-change acquisition and checks."""

from __future__ import annotations

from pytest_bdd import parsers, then, when

from api.sessiondata.models.entry import WorktreeBodyResponse
from sdk.client import BaqylauClient
from sdk.state import SessionSnapshot
from tests.e2e.testkit import selectors
from tests.e2e.testkit.policy import WaitPolicy
from tests.e2e.testkit.references import (
    Sessions,
    WorktreeChangeRef,
    WorktreeChanges,
    Works,
)
from tests.e2e.testkit.repository import RepositoryWorkspace


def _change(
    snapshot: SessionSnapshot,
    reference: WorktreeChangeRef,
) -> WorktreeBodyResponse:
    found = [
        entry.body
        for entry in snapshot.entries
        if entry.entry_id == reference.entry_id
        and isinstance(entry.body, WorktreeBodyResponse)
    ]
    if len(found) != 1:
        raise AssertionError(
            f"worktree change {reference.entry_id!r} has {len(found)} matches"
        )
    return found[0]


@when(parsers.parse(
    'I name the {action} worktree change in work "{work_name}" "{change_name}"'
))
def name_worktree_change(
    client: BaqylauClient,
    works: Works,
    worktree_changes: WorktreeChanges,
    wait_policy: WaitPolicy,
    action: str,
    work_name: str,
    change_name: str,
) -> None:
    work = works.get(work_name)
    worktree_changes.bind(
        change_name,
        selectors.worktree_change(
            client.sessions.watch(work.session),
            turn_reference=work.turn,
            action=action,
            timeout=wait_policy.feed,
        ),
    )


@then(parsers.parse('worktree change "{name}" has state {state}'))
def worktree_change_has_state(
    client: BaqylauClient,
    worktree_changes: WorktreeChanges,
    name: str,
    state: str,
) -> None:
    reference = worktree_changes.get(name)
    assert _change(client.sessions.snapshot(reference.session), reference).state == state


@then(parsers.parse(
    'session "{session_name}" reports the exact {state} isolated repository state'
))
def session_reports_repository_state(
    client: BaqylauClient,
    sessions: Sessions,
    repository_workspace: RepositoryWorkspace,
    session_name: str,
    state: str,
) -> None:
    if state not in {"clean", "dirty"}:
        raise AssertionError(f"unknown repository state {state!r}")
    repository = client.sessions.snapshot(sessions.get(session_name)).data.repository
    assert repository is not None
    assert repository.branch == repository_workspace.branch
    assert repository.worktree == repository_workspace.worktree
    assert repository.dirty is (state == "dirty")


@when("I remove the isolated linked worktree")
def remove_isolated_linked_worktree(
    repository_workspace: RepositoryWorkspace,
) -> None:
    repository_workspace.remove_linked_worktree()
