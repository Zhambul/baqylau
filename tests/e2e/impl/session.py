"""Starting a session, and what it reports about itself.

The GIVEN starts the harness. It used to record intent and leave the launch to
the first prompt, which made every feature file open with a sentence that said
"given a session" and meant "given nothing yet" — and it meant a scenario could
not exist without also having something to say. A session is a thing you have
before you speak to it, so this is where it begins.
"""

from __future__ import annotations

import pytest
from pytest_bdd import given, parsers, then

from impl.world import World, diagnostics
from support import harness as harness_support
from support import observe
from support.daemon import Daemon

SESSION_ANNOUNCE_TIMEOUT_SECONDS = 120.0
# A turn is over when the lead actor's own status says it has gone quiet.
# Background work is a separate scenario's problem; either state means the
# harness has stopped answering.
TURN_ENDED = ("awaiting_response", "awaiting_background")


# Two patterns, one implementation: pytest-bdd matches a whole sentence, so an
# optional capture has to be a second sentence. The prompt-less form is for a
# scenario that says what to send in its own WHEN.
@given(parsers.parse("a {harness} session on {model} at {effort} effort"))
def _a_session(
    world: World, pytestconfig: pytest.Config, daemon: Daemon, workspace: str,
    harness: str, model: str, effort: str,
) -> None:
    _launch(world, pytestconfig, daemon, workspace, harness, model, effort, None)


@given(parsers.parse("a {harness} session on {model} at {effort} effort with prompt '{prompt}'"))
def _a_session_with_a_prompt(
    world: World, pytestconfig: pytest.Config, daemon: Daemon, workspace: str,
    harness: str, model: str, effort: str, prompt: str,
) -> None:
    _launch(world, pytestconfig, daemon, workspace, harness, model, effort, prompt)


def _launch(
    world: World, pytestconfig: pytest.Config, daemon: Daemon, workspace: str,
    harness: str, model: str, effort: str, prompt: str | None,
) -> None:
    """The launch the dashboard's own new-session form makes.

    POST /api/sessions, and everything after it is the product's: the launcher
    builds the plan, the terminal adapter opens the window, the harness's hooks
    find their way back by the port the daemon passed down.

    The model and effort come from the scenario unless --e2e-model / --e2e-effort
    overrode them, which is what lets a new model be tried against the whole
    suite without editing an Examples table.
    """
    world.harness = harness
    world.model = str(pytestconfig.getoption("--e2e-model") or model)
    world.effort = str(pytestconfig.getoption("--e2e-effort") or effort)
    world.prompt = prompt or ""
    # Taken BEFORE the launch: the session this scenario is about is the one that
    # was not there a moment ago (see observe.session_started_in).
    known = observe.session_ids(daemon)
    world.live = harness_support.launch(
        daemon,
        world.harness,
        workspace=workspace,
        prompt=prompt,
        model=world.model,
        effort=world.effort,
    )
    live = world.live
    world.session_id = observe.until(
        lambda: (
            f"{world.harness} to announce a session in {workspace}\n"
            f"  window: {live.window_id}\n"
            # IF THIS TIMED OUT AND THE HARNESS IS SITTING ON A TRUST PROMPT:
            # both harnesses ask once, per directory, before doing anything — and
            # neither announces a session until it is answered, so there is no
            # session to address and nothing this suite can press. This rig does
            # not answer it and deliberately holds no knowledge of either
            # vendor's trust format. Run the harness in the workspace by hand
            # once, say yes, and re-run; every scenario afterwards is unaffected.
            + diagnostics(daemon, None)
        ),
        lambda: observe.session_started_in(daemon, world.harness, workspace, known),
        timeout=SESSION_ANNOUNCE_TIMEOUT_SECONDS,
    )


@then(parsers.parse("the turn ends within {minutes:d} minutes"))
def _the_turn_ends(world: World, daemon: Daemon, minutes: int) -> None:
    assert world.session_id is not None
    observe.until(
        f"the turn to end (the lead's status one of {TURN_ENDED})",
        lambda: observe.status(daemon, str(world.session_id)) in TURN_ENDED,
        timeout=minutes * 60,
    )


@then(parsers.parse("the session reports the model {model}"))
def _reports_the_model(world: World, daemon: Daemon, model: str) -> None:
    # `world.model`, not the step's `model`: --e2e-model may have replaced what
    # the Examples table asked for, and the assertion is about what was LAUNCHED.
    # Read off the LEAD actor: a model is something an actor runs on, and a
    # session with subagents has one per actor.
    reported = observe.lead(daemon, str(world.session_id)).model
    assert observe.model_matches(reported, str(world.model)), (
        f"launched with model {world.model!r}, the lead reports {reported!r}"
    )


@then(parsers.parse("the session reports {effort} effort"))
def _reports_the_effort(world: World, daemon: Daemon, effort: str) -> None:
    reported = observe.lead(daemon, str(world.session_id)).effort
    assert reported == world.effort, (
        f"launched at effort {world.effort!r}, the lead reports {reported!r}"
    )
