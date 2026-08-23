"""Single-fact checks for session aggregate counters."""

from __future__ import annotations

from collections.abc import Callable

from pytest_bdd import parsers, then

from sdk.client import BaqylauClient
from sdk.state import SessionSnapshot
from tests.e2e.testkit import selectors
from tests.e2e.testkit.policy import WaitPolicy
from tests.e2e.testkit.references import Sessions, Turns


def _wait(
    client: BaqylauClient,
    sessions: Sessions,
    policy: WaitPolicy,
    session_name: str,
    description: str,
    condition: Callable[[SessionSnapshot], bool],
) -> None:
    session = sessions.get(session_name)
    client.sessions.watch(session).wait(
        description,
        lambda snapshot: True if condition(snapshot) else None,
        timeout=policy.feed,
    )


@then(parsers.parse('session "{name}" has at least {count:d} prompts'))
def prompt_count(
    client: BaqylauClient, sessions: Sessions, wait_policy: WaitPolicy, name: str, count: int
) -> None:
    _wait(
        client,
        sessions,
        wait_policy,
        name,
        f"session {name!r} to have at least {count} prompts",
        lambda snapshot: sum(item.statistics.prompt_count for item in snapshot.data.actors) >= count,
    )


@then(parsers.parse('session "{name}" has at least {count:d} shell commands'))
@then(parsers.parse('session "{name}" has at least {count:d} shell command'))
def shell_count(
    client: BaqylauClient, sessions: Sessions, wait_policy: WaitPolicy, name: str, count: int
) -> None:
    _wait(
        client,
        sessions,
        wait_policy,
        name,
        f"session {name!r} to have at least {count} shell commands",
        lambda snapshot: sum(
            item.statistics.shell_command_count for item in snapshot.data.actors
        ) >= count,
    )


@then(parsers.parse('session "{name}" has at least {count:d} failed shell command'))
def failed_shell_count(
    client: BaqylauClient, sessions: Sessions, wait_policy: WaitPolicy, name: str, count: int
) -> None:
    _wait(
        client,
        sessions,
        wait_policy,
        name,
        f"session {name!r} to have at least {count} failed shell command",
        lambda snapshot: sum(
            item.statistics.failed_shell_command_count for item in snapshot.data.actors
        ) >= count,
    )


@then(parsers.parse('session "{name}" has at least {count:d} file operation'))
def file_count(
    client: BaqylauClient, sessions: Sessions, wait_policy: WaitPolicy, name: str, count: int
) -> None:
    _wait(
        client,
        sessions,
        wait_policy,
        name,
        f"session {name!r} to have at least {count} file operation",
        lambda snapshot: sum(item.statistics.file_count for item in snapshot.data.actors) >= count,
    )


@then(parsers.parse('session "{name}" has added lines'))
def has_added_lines(
    client: BaqylauClient, sessions: Sessions, wait_policy: WaitPolicy, name: str
) -> None:
    _wait(
        client,
        sessions,
        wait_policy,
        name,
        f"session {name!r} to have added lines",
        lambda snapshot: sum(item.statistics.lines_added for item in snapshot.data.actors) > 0,
    )


@then(parsers.parse('session "{name}" has removed lines'))
def has_removed_lines(
    client: BaqylauClient, sessions: Sessions, wait_policy: WaitPolicy, name: str
) -> None:
    _wait(
        client,
        sessions,
        wait_policy,
        name,
        f"session {name!r} to have removed lines",
        lambda snapshot: sum(item.statistics.lines_removed for item in snapshot.data.actors) > 0,
    )


@then(parsers.parse('session "{name}" used tool {tool}'))
def used_tool(
    client: BaqylauClient,
    sessions: Sessions,
    wait_policy: WaitPolicy,
    name: str,
    tool: str,
) -> None:
    _wait(
        client,
        sessions,
        wait_policy,
        name,
        f"session {name!r} to report tool {tool!r}",
        lambda snapshot: any(
            row.tool == tool and row.count > 0
            for actor in snapshot.data.actors
            for row in actor.statistics.tool_counts
        ),
    )


@then(parsers.parse('session "{name}" has positive active time'))
def active_time(
    client: BaqylauClient, sessions: Sessions, wait_policy: WaitPolicy, name: str
) -> None:
    _wait(
        client,
        sessions,
        wait_policy,
        name,
        f"session {name!r} to have positive active time",
        lambda snapshot: max(item.statistics.active_seconds for item in snapshot.data.actors) > 0,
    )


@then(parsers.parse('session "{name}" has positive input token usage'))
def input_usage(
    client: BaqylauClient, sessions: Sessions, wait_policy: WaitPolicy, name: str
) -> None:
    _wait(
        client,
        sessions,
        wait_policy,
        name,
        f"session {name!r} to have positive input token usage",
        lambda snapshot: sum(
            item.usage.tokens.input_tokens or 0 for item in snapshot.data.actors
        ) > 0,
    )


@then(parsers.parse('session "{name}" has positive output token usage'))
def output_usage(
    client: BaqylauClient, sessions: Sessions, wait_policy: WaitPolicy, name: str
) -> None:
    _wait(
        client,
        sessions,
        wait_policy,
        name,
        f"session {name!r} to have positive output token usage",
        lambda snapshot: sum(
            item.usage.tokens.output_tokens or 0 for item in snapshot.data.actors
        ) > 0,
    )


@then(parsers.parse('turn "{turn_name}" has exactly {count:d} backgrounded command'))
def turn_backgrounded_count(
    client: BaqylauClient,
    turns: Turns,
    turn_name: str,
    count: int,
) -> None:
    turn = turns.get(turn_name)
    snapshot = client.sessions.snapshot(turn.session)
    found = [
        item
        for item in snapshot.shells()
        if (
            item.turn_id == turn.turn_id
            or selectors.cursor_is_in_turn(snapshot, turn, item.started_cursor)
        )
        and (item.backgrounded or item.execution == "background")
    ]
    assert len(found) == count, f"turn {turn_name!r} has {len(found)} backgrounded commands"


@then(parsers.parse('session "{name}" has exactly {count:d} historical job'))
def historical_job_count(client: BaqylauClient, sessions: Sessions, name: str, count: int) -> None:
    snapshot = client.sessions.snapshot(sessions.get(name))
    found = sum(actor.background.background_job_count for actor in snapshot.data.actors)
    assert found == count, f"session {name!r} has {found} historical jobs"


@then(parsers.parse('session "{name}" has no running work'))
def no_running_work(client: BaqylauClient, sessions: Sessions, name: str) -> None:
    snapshot = client.sessions.snapshot(sessions.get(name))
    found = {
        shell_id
        for actor in snapshot.data.actors
        for shell_id in actor.background.running_shell_ids
    }
    assert not found, f"session {name!r} still has running work: {sorted(found)}"
