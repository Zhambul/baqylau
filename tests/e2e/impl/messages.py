"""Saying something to a session, and reading what came back.

The send is the product's own gesture — POST .../controls/send-text, the same
request the composer makes — so what this suite exercises is the path a person's
message takes: typed into the harness's TUI by the harness's own controller,
through the terminal the daemon owns. It used to be the launch's `initial_text`,
which meant every scenario got exactly one thing to say and the gesture that
delivers the rest was never live-tested at all.
"""

from __future__ import annotations

from pytest_bdd import parsers, then, when

from api.sessiondata.models.entry import EntryResponse
from impl.world import World, diagnostics, feed
from support import observe
from support.daemon import Daemon

# How long a fact may take to travel from the harness's file to the feed after
# the turn is already over: one interpreter poll plus slack.
FEED_SETTLE_TIMEOUT_SECONDS = 60.0
# How long to wait for the harness's TUI to accept a message. The gesture types
# into a live screen, so it can legitimately decline while the composer is busy.
SEND_TIMEOUT_SECONDS = 60.0


@when(parsers.parse("I send message '{prompt}'"))
def _i_send_message(world: World, daemon: Daemon, prompt: str) -> None:
    """The send-text gesture, retried until the harness takes it.

    A decline is not a failure here: the controller types into the TUI, and a TUI
    mid-redraw or still finishing a turn legitimately refuses. Retrying is what a
    person does, and the 409 says "not now" rather than "no".
    """
    assert world.session_id is not None, "no session to send to"
    session_id = str(world.session_id)
    world.prompt = prompt
    world.expected_prompt_count = (
        sum(actor.statistics.prompt_count for actor in observe.actors(daemon, session_id)) + 1
    )
    answers: list[str] = []

    def sent() -> bool:
        status, body = daemon.post(
            f"/api/sessions/{session_id}/controls/send-text",
            {"request_id": "e2e-send-%d" % len(answers), "text": prompt},
        )
        answers.append(f"{status} {body[:120]}")
        return status == 200

    observe.until(
        lambda: "the harness to accept the message; it answered "
                + " | ".join(answers[-2:]) + diagnostics(daemon, session_id),
        sent,
        timeout=SEND_TIMEOUT_SECONDS,
    )


@then(parsers.parse("the feed shows my prompt '{text}'"))
def _the_feed_shows_my_prompt(world: World, daemon: Daemon, text: str) -> None:
    observe.until(
        lambda: (
            f"a prompt entry reading {text!r}; the feed has "
            f"{[observe.text(entry)[:80] for entry in observe.prompts(feed(world, daemon))]}"
        ),
        lambda: any(
            observe.text(entry).strip() == text
            for entry in observe.prompts(feed(world, daemon))
        ),
        timeout=FEED_SETTLE_TIMEOUT_SECONDS,
    )


@then(parsers.parse("the assistant ends the turn with '{text}'"))
def _the_assistant_ends_the_turn_with(world: World, daemon: Daemon, text: str) -> None:
    # Waited for, not sampled: the turn ends on a HOOK, which is instant, while
    # the answer itself arrives in the harness's own record, which is polled.
    # Asserting the moment the turn ends would fail on the lag between the two —
    # and would pass or fail depending on the machine, which is worse.
    #
    # Two claims in one sentence, deliberately: that the text arrived, and that
    # the feed knows this message is where the model STOPPED. The second is the
    # one a harness release breaks quietly — the text keeps arriving.
    #
    # Both are inside the WAIT, and the wait is for the LAST bubble rather than
    # for any bubble. A model that narrates while it works has said several things
    # before it answers — "Waiting for the subagent to complete." — so a wait that
    # ends at the first bubble reads the answer as whatever was on screen when the
    # step happened to run. That is lag, not drift, and it failed a scenario whose
    # answer arrived two seconds later.
    def ended_on_it() -> list[EntryResponse] | None:
        items = feed(world, daemon)
        answers = observe.assistant_messages(items)
        if not answers or observe.text(answers[-1]).strip() != text:
            return None
        enders = observe.turn_enders(items)
        return answers if enders and enders[-1].entry_id == answers[-1].entry_id else None

    def missing() -> str:
        bubbles = [
            (observe.text(entry)[:40], entry.body.phase)
            for entry in observe.assistant_messages(feed(world, daemon))
        ]
        return (
            f"the assistant to end the turn on {text!r} and the feed to mark that "
            f"message as where it stopped; the bubbles read {bubbles}"
        )

    observe.until(missing, ended_on_it, timeout=FEED_SETTLE_TIMEOUT_SECONDS)
