"""Named shell acquisition, actions, and single-fact checks."""

from __future__ import annotations

from pytest_bdd import parsers, then, when

from sdk.client import BaqylauClient
from sdk.state import SessionSnapshot, ShellState
from tests.e2e.testkit import selectors
from tests.e2e.testkit.policy import WaitPolicy
from tests.e2e.testkit.references import ShellRef, Shells, Turns


def _shell(snapshot: SessionSnapshot, reference: ShellRef) -> ShellState:
    found = [item for item in snapshot.shells() if item.shell_id == reference.shell_id]
    if len(found) != 1:
        raise AssertionError(
            f"shell {reference.shell_id!r} has {len(found)} matches in session {snapshot.session_id!r}"
        )
    return found[0]


def _wait_for_output(
    client: BaqylauClient,
    reference: ShellRef,
    wait_policy: WaitPolicy,
    name: str,
    text: str,
) -> None:
    client.sessions.watch(reference.session).wait(
        f"command {name!r} output to contain {text!r}",
        lambda snapshot: True if text in _shell(snapshot, reference).output else None,
        timeout=wait_policy.background,
    )


@when(parsers.parse(
    'I name the only shell command in turn "{turn_name}" containing \'{command}\' "{name}"'
))
@when(parsers.parse(
    'I name the only shell command in work "{turn_name}" containing \'{command}\' "{name}"'
))
def name_shell_command(
    client: BaqylauClient,
    turns: Turns,
    shells: Shells,
    wait_policy: WaitPolicy,
    turn_name: str,
    command: str,
    name: str,
) -> None:
    turn = turns.get(turn_name)
    found = selectors.shell(
        client.sessions.watch(turn.session),
        turn_reference=turn,
        command_contains=command,
        timeout=wait_policy.feed,
    )
    shells.bind(name, found)


@when(parsers.parse(
    'I name the only running foreground command in turn "{turn_name}" containing \'{command}\' "{name}"'
))
@when(parsers.parse(
    'I name the only running foreground command in work "{turn_name}" containing \'{command}\' "{name}"'
))
def name_running_foreground_command(
    client: BaqylauClient,
    turns: Turns,
    shells: Shells,
    wait_policy: WaitPolicy,
    turn_name: str,
    command: str,
    name: str,
) -> None:
    turn = selectors.turn(
        client.sessions.watch(turns.get(turn_name).session),
        turns.get(turn_name),
        wait_policy.turn,
    )
    turns.replace(turn_name, turn)
    found = selectors.shell(
        client.sessions.watch(turn.session),
        turn_reference=turn,
        command_contains=command,
        predicate=lambda item: (
            item.execution == "foreground" and item.state is None and not item.backgrounded
        ),
        timeout=wait_policy.turn,
    )
    shells.bind(name, found)


@when(parsers.parse(
    'I name the only background job in turn "{turn_name}" containing \'{command}\' "{name}"'
))
@when(parsers.parse(
    'I name the only background job in work "{turn_name}" containing \'{command}\' "{name}"'
))
def name_background_job(
    client: BaqylauClient,
    turns: Turns,
    shells: Shells,
    wait_policy: WaitPolicy,
    turn_name: str,
    command: str,
    name: str,
) -> None:
    turn = turns.get(turn_name)
    found = selectors.shell(
        client.sessions.watch(turn.session),
        turn_reference=turn,
        command_contains=command,
        predicate=lambda item: item.execution == "background" or item.backgrounded,
        timeout=wait_policy.feed,
    )
    shells.bind(name, found)


@when(parsers.parse(
    'I name the only monitor in turn "{turn_name}" containing \'{command}\' "{name}"'
))
@when(parsers.parse(
    'I name the only monitor in work "{turn_name}" containing \'{command}\' "{name}"'
))
def name_monitor(
    client: BaqylauClient,
    turns: Turns,
    shells: Shells,
    wait_policy: WaitPolicy,
    turn_name: str,
    command: str,
    name: str,
) -> None:
    turn = turns.get(turn_name)
    found = selectors.shell(
        client.sessions.watch(turn.session),
        turn_reference=turn,
        command_contains=command,
        predicate=lambda item: item.execution == "monitor",
        timeout=wait_policy.feed,
    )
    shells.bind(name, found)


@then(parsers.parse('command "{name}" has state {state}'))
def command_has_state(
    client: BaqylauClient,
    shells: Shells,
    wait_policy: WaitPolicy,
    name: str,
    state: str,
) -> None:
    reference = shells.get(name)
    client.sessions.watch(reference.session).wait(
        f"command {name!r} to have state {state!r}",
        lambda snapshot: True if _shell(snapshot, reference).state == state else None,
        timeout=wait_policy.feed,
    )


@then(parsers.parse('command "{name}" has output containing \'{text}\''))
def command_has_output(
    client: BaqylauClient,
    shells: Shells,
    wait_policy: WaitPolicy,
    name: str,
    text: str,
) -> None:
    reference = shells.get(name)
    _wait_for_output(client, reference, wait_policy, name, text)


@then(parsers.parse('command "{name}" has exit code {exit_code:d}'))
def command_has_exit_code(
    client: BaqylauClient,
    shells: Shells,
    wait_policy: WaitPolicy,
    name: str,
    exit_code: int,
) -> None:
    reference = shells.get(name)
    client.sessions.watch(reference.session).wait(
        f"command {name!r} to have exit code {exit_code}",
        lambda snapshot: True if _shell(snapshot, reference).exit_code == exit_code else None,
        timeout=wait_policy.feed,
    )


@then(parsers.parse('command "{name}" becomes a background job'))
def command_becomes_background_job(
    client: BaqylauClient,
    shells: Shells,
    wait_policy: WaitPolicy,
    name: str,
) -> None:
    reference = shells.get(name)
    client.sessions.watch(reference.session).wait(
        f"command {name!r} to report that it was backgrounded",
        lambda snapshot: True if _shell(snapshot, reference).backgrounded else None,
        timeout=wait_policy.feed,
    )


@then(parsers.parse('job "{name}" is running'))
@then(parsers.parse('monitor "{name}" is running'))
def background_work_is_running(
    client: BaqylauClient,
    shells: Shells,
    wait_policy: WaitPolicy,
    name: str,
) -> None:
    reference = shells.get(name)
    client.sessions.watch(reference.session).wait(
        f"background work {name!r} to be in the running set",
        lambda snapshot: (
            True
            if reference.shell_id
            in {item for actor in snapshot.data.actors for item in actor.background.running_shell_ids}
            else None
        ),
        timeout=wait_policy.feed,
    )


@then(parsers.parse('job "{name}" has output containing \'{text}\''))
def job_has_output(
    client: BaqylauClient,
    shells: Shells,
    wait_policy: WaitPolicy,
    name: str,
    text: str,
) -> None:
    _wait_for_output(client, shells.get(name), wait_policy, name, text)


@then(parsers.parse('monitor "{name}" has event containing \'{text}\''))
def monitor_has_event(
    client: BaqylauClient,
    shells: Shells,
    wait_policy: WaitPolicy,
    name: str,
    text: str,
) -> None:
    reference = shells.get(name)
    client.sessions.watch(reference.session).wait(
        f"monitor {name!r} event to contain {text!r}",
        lambda snapshot: True if text in _shell(snapshot, reference).status else None,
        timeout=wait_policy.background,
    )


@then(parsers.parse('job "{name}" ends'))
@then(parsers.parse('monitor "{name}" ends'))
def background_work_ends(
    client: BaqylauClient,
    shells: Shells,
    wait_policy: WaitPolicy,
    name: str,
) -> None:
    reference = shells.get(name)
    client.sessions.watch(reference.session).wait(
        f"background work {name!r} to leave the running set",
        lambda snapshot: (
            True
            if reference.shell_id
            not in {item for actor in snapshot.data.actors for item in actor.background.running_shell_ids}
            else None
        ),
        timeout=wait_policy.background,
    )
