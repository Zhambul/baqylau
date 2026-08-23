"""Named file operation acquisition and single-fact checks."""

from __future__ import annotations

import os

from pytest_bdd import given, parsers, then, when

from api.sessiondata.models.entry import FileBodyResponse
from sdk.client import BaqylauClient
from sdk.state import SessionSnapshot
from tests.e2e.testkit import selectors
from tests.e2e.testkit.policy import WaitPolicy
from tests.e2e.testkit.references import FileOperationRef, FileOperations, Turns


def _operation(snapshot: SessionSnapshot, reference: FileOperationRef) -> FileBodyResponse:
    found = [
        entry.body
        for entry in snapshot.entries
        if entry.entry_id == reference.entry_id and isinstance(entry.body, FileBodyResponse)
    ]
    if len(found) != 1:
        raise AssertionError(f"file operation {reference.entry_id!r} has {len(found)} matches")
    return found[0]


@given("the file operation fixture does not exist")
def file_operation_fixture_does_not_exist(file_operation_path: str) -> None:
    assert not os.path.exists(file_operation_path)


@then("the file operation fixture is absent")
def file_operation_fixture_is_absent(file_operation_path: str) -> None:
    assert not os.path.exists(file_operation_path)


@when(parsers.parse(
    'I name the {action} fixture operation in turn "{turn_name}" "{operation_name}"'
))
@when(parsers.parse(
    'I name the {action} fixture operation in work "{turn_name}" "{operation_name}"'
))
def name_fixture_operation(
    client: BaqylauClient,
    turns: Turns,
    file_operations: FileOperations,
    file_operation_path: str,
    wait_policy: WaitPolicy,
    action: str,
    turn_name: str,
    operation_name: str,
) -> None:
    turn = turns.get(turn_name)
    found = selectors.file_operation(
        client.sessions.watch(turn.session),
        turn_reference=turn,
        path=file_operation_path,
        action=action,
        timeout=wait_policy.feed,
    )
    file_operations.bind(operation_name, found)


@when(parsers.parse(
    'I name the {action} operation in turn "{turn_name}" for workspace file '
    '\'{relative_path}\' "{operation_name}"'
))
@when(parsers.parse(
    'I name the {action} operation in work "{turn_name}" for workspace file '
    '\'{relative_path}\' "{operation_name}"'
))
def name_workspace_file_operation(
    client: BaqylauClient,
    workspace: str,
    turns: Turns,
    file_operations: FileOperations,
    wait_policy: WaitPolicy,
    action: str,
    turn_name: str,
    relative_path: str,
    operation_name: str,
) -> None:
    turn = turns.get(turn_name)
    found = selectors.file_operation(
        client.sessions.watch(turn.session),
        turn_reference=turn,
        path=os.path.join(workspace, relative_path),
        action=action,
        timeout=wait_policy.feed,
    )
    file_operations.bind(operation_name, found)


@then(parsers.parse('file operation "{name}" has state {state}'))
def file_operation_has_state(
    client: BaqylauClient,
    file_operations: FileOperations,
    wait_policy: WaitPolicy,
    name: str,
    state: str,
) -> None:
    reference = file_operations.get(name)
    client.sessions.watch(reference.session).wait(
        f"file operation {name!r} to have state {state!r}",
        lambda snapshot: True if _operation(snapshot, reference).state == state else None,
        timeout=wait_policy.feed,
    )


@then(parsers.parse('file operation "{name}" has content containing \'{text}\''))
def file_operation_has_content(
    client: BaqylauClient,
    file_operations: FileOperations,
    wait_policy: WaitPolicy,
    name: str,
    text: str,
) -> None:
    reference = file_operations.get(name)

    def contains(snapshot: SessionSnapshot) -> bool | None:
        content = _operation(snapshot, reference).content
        return True if content is not None and text in content.text else None

    client.sessions.watch(reference.session).wait(
        f"file operation {name!r} content to contain {text!r}",
        contains,
        timeout=wait_policy.feed,
    )


@then(parsers.parse('file operation "{name}" has added lines'))
def file_operation_has_added_lines(
    client: BaqylauClient,
    file_operations: FileOperations,
    wait_policy: WaitPolicy,
    name: str,
) -> None:
    reference = file_operations.get(name)
    client.sessions.watch(reference.session).wait(
        f"file operation {name!r} to have added lines",
        lambda snapshot: True if (_operation(snapshot, reference).lines_added or 0) > 0 else None,
        timeout=wait_policy.feed,
    )


@then(parsers.parse('file operation "{name}" has removed lines'))
def file_operation_has_removed_lines(
    client: BaqylauClient,
    file_operations: FileOperations,
    wait_policy: WaitPolicy,
    name: str,
) -> None:
    reference = file_operations.get(name)
    client.sessions.watch(reference.session).wait(
        f"file operation {name!r} to have removed lines",
        lambda snapshot: True if (_operation(snapshot, reference).lines_removed or 0) > 0 else None,
        timeout=wait_policy.feed,
    )


@then(parsers.parse('the file operation fixture contains \'{text}\''))
def file_operation_fixture_contains(file_operation_path: str, text: str) -> None:
    with open(file_operation_path, encoding="utf-8") as fixture:
        content = fixture.read()
    assert text in content, f"file operation fixture does not contain {text!r}: {content!r}"
