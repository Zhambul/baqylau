"""Named compaction acquisition and lifecycle checks."""

from __future__ import annotations

from pytest_bdd import parsers, then, when

from sdk.client import BaqylauClient
from api.sessiondata.models.entry import CompactionFinishedBodyResponse
from sdk.state import CompactionState, SessionSnapshot
from tests.e2e.testkit import selectors
from tests.e2e.testkit.policy import WaitPolicy
from tests.e2e.testkit.references import CompactionRef, Compactions, Controls, Sessions


def _compaction(snapshot: SessionSnapshot, reference: CompactionRef) -> CompactionState:
    found = [
        item
        for item in snapshot.compactions()
        if item.actor_id == reference.actor_id
        and item.started_cursor == reference.started_cursor
    ]
    if len(found) != 1:
        raise AssertionError(
            f"compaction at cursor {reference.started_cursor} has {len(found)} matches"
        )
    return found[0]


@when(parsers.parse(
    'I name the compaction in session "{session_name}" after control '
    '"{control_name}" "{compaction_name}"'
))
def name_compaction(
    client: BaqylauClient,
    sessions: Sessions,
    controls: Controls,
    compactions: Compactions,
    wait_policy: WaitPolicy,
    session_name: str,
    control_name: str,
    compaction_name: str,
) -> None:
    control = controls.get(control_name)
    found = selectors.compaction(
        client.sessions.watch(sessions.get(session_name)),
        after_cursor=control.cursor_before,
        timeout=wait_policy.background,
    )
    compactions.bind(compaction_name, found)


@then(parsers.parse('compaction "{name}" finishes'))
def compaction_finishes(
    client: BaqylauClient,
    compactions: Compactions,
    wait_policy: WaitPolicy,
    name: str,
) -> None:
    reference = compactions.get(name)
    client.sessions.watch(reference.session).wait(
        f"compaction {name!r} to finish",
        lambda snapshot: True if _compaction(snapshot, reference).finished else None,
        timeout=wait_policy.background,
    )


@then(parsers.parse('compaction "{name}" leaves its actor ready'))
def compaction_leaves_actor_ready(
    client: BaqylauClient,
    compactions: Compactions,
    wait_policy: WaitPolicy,
    name: str,
) -> None:
    reference = compactions.get(name)
    client.sessions.watch(reference.session).wait(
        f"compaction {name!r} actor to leave compacting state",
        lambda snapshot: (
            True
            if _compaction(snapshot, reference).finished
            and not snapshot.actor(reference.actor_id).context.compacting
            else None
        ),
        timeout=wait_policy.feed,
    )


@then(parsers.parse('compaction "{name}" has one finished feed entry'))
def compaction_has_one_finished_feed_entry(
    client: BaqylauClient,
    compactions: Compactions,
    wait_policy: WaitPolicy,
    name: str,
) -> None:
    reference = compactions.get(name)

    def one_finish(snapshot: SessionSnapshot) -> bool | None:
        lifecycle = _compaction(snapshot, reference)
        if not lifecycle.finished:
            return None
        finishes = [
            entry
            for entry in snapshot.entries
            if entry.actor_id == reference.actor_id
            and entry.cursor > reference.started_cursor
            and isinstance(entry.body, CompactionFinishedBodyResponse)
        ]
        assert len(finishes) == 1, (
            f"compaction {name!r} has {len(finishes)} finished feed entries"
        )
        return True

    client.sessions.watch(reference.session).wait(
        f"compaction {name!r} to have one finished feed entry",
        one_finish,
        timeout=wait_policy.feed,
    )
