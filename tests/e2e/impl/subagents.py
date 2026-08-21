"""Work handed to another actor, and how it is attributed.

Two facts from two sources, and only together do they mean a subagent ran: the
ASSIGNMENT is what the parent's own tool call asked for, and the ACTOR is the
child announcing itself from inside its own process. That split is why the reads
here differ — an assignment is read across the session because its two halves
belong to two actors, while a command is read at one actor because that is the
attribution being asserted.
"""

from __future__ import annotations

from pytest_bdd import parsers, then, when

from api.sessiondata.models.session_data import ActorResponse
from impl.world import World, folded_assignments
from support import observe
from support.daemon import Daemon

FEED_SETTLE_TIMEOUT_SECONDS = 60.0


@then(parsers.parse("the session lists a subagent '{name}'"))
def _the_session_lists_a_subagent(world: World, daemon: Daemon, name: str) -> None:
    """A second ACTOR in the session, launched by the first.

    The actor, not the assignment: those are two facts from two sources, and only
    together do they mean a subagent ran. The assignment is what the lead's own
    tool call said it wanted (`Task` → an assignment entry); the actor is the
    child announcing itself from inside its own process (Claude Code's
    SubagentStart hook → an actor row). A session showing one without the other
    is a session where the delegation was recorded and the delegate was never
    seen, or the reverse.
    """
    def found() -> ActorResponse | None:
        for actor in observe.subagents(daemon, str(world.session_id)):
            if name.lower() in str(actor.name).lower():
                return actor
        return None

    def missing() -> str:
        listed = [(actor.name, actor.role, actor.state)
                  for actor in observe.actors(daemon, str(world.session_id))]
        return f"a subagent named {name!r}; the session lists the actors {listed}"

    world.actor = observe.until(missing, found, timeout=FEED_SETTLE_TIMEOUT_SECONDS)


@then(parsers.parse("that subagent finishes within {minutes:d} minutes"))
def _that_subagent_finishes(world: World, daemon: Daemon, minutes: int) -> None:
    """…and it is the CHILD's own loop ending that says so.

    Claude Code fires SubagentStop from inside the agent's process, which is the
    only report that survives an agent leaving a background command behind — the
    parent's notification is suppressed while one is alive, and agents that
    spawned one used to read as running forever.
    """
    assert world.actor is not None, "no subagent has been found yet"
    actor_id = str(world.actor.actor_id)

    def finished() -> ActorResponse | None:
        for actor in observe.subagents(daemon, str(world.session_id)):
            if str(actor.actor_id) == actor_id and actor.state == "finished":
                return actor
        return None

    actor = observe.until(f"the subagent {actor_id!r} to report its own end", finished,
                          timeout=minutes * 60)
    assert actor.finished_at is not None, "the subagent finished without an end time"


@when("I look at that subagent")
def _i_look_at_that_subagent(world: World) -> None:
    """Switches the viewpoint. The filter is the ENTRY's own `actor_id`, which is
    what makes a feed read at one actor evidence about attribution rather than
    just about arrival — the server does not narrow it for us."""
    assert world.actor is not None, "no subagent has been found yet"
    world.viewpoint = str(world.actor.actor_id)


@when("I look at the session itself")
def _i_look_at_the_session_itself(world: World) -> None:
    world.viewpoint = None


@then(parsers.parse("the feed shows a {state} agent assignment"))
def _the_feed_shows_an_agent_assignment(world: World, daemon: Daemon, state: str) -> None:
    """The delegation as the lead's feed draws it — one block whose outcome is
    the AGENT's, arriving long after the tool call that started it returned."""
    def found() -> observe.Assignment | None:
        for assignment in folded_assignments(world, daemon):
            if assignment.state == state:
                return assignment
        return None

    def missing() -> str:
        seen = [(item.assignment_id, item.state) for item in folded_assignments(world, daemon)]
        return f"a {state} agent assignment; the session has {seen}"

    world.assignment = observe.until(missing, found, timeout=FEED_SETTLE_TIMEOUT_SECONDS)


@then(parsers.parse("the feed shows {count:d} succeeded agent assignments"))
def _the_feed_shows_n_assignments(world: World, daemon: Daemon, count: int) -> None:
    """Two delegations from one turn have to stay two.

    A count, because the failure this catches is not a missing event but a
    COLLAPSED one: canonical identity is derived from the subject's id
    (`stable_event_id`), so two assignments that end up sharing one — an id
    defaulted to the empty string, say — become a single row, and every other
    assertion about an assignment still passes. The fold below is keyed by
    assignment id, so a collapse is a count this step cannot reach.
    """
    def counted() -> list[observe.Assignment] | None:
        succeeded = [item for item in folded_assignments(world, daemon)
                     if item.state == "succeeded"]
        return succeeded if len(succeeded) >= count else None

    def missing() -> str:
        seen = [(item.assignment_id, item.state) for item in folded_assignments(world, daemon)]
        return f"{count} succeeded agent assignments; the session has {seen}"

    found = observe.until(missing, counted, timeout=FEED_SETTLE_TIMEOUT_SECONDS)
    identities = {item.assignment_id for item in found}
    assert len(identities) >= count, f"{len(found)} assignments share {identities}"


@then(parsers.parse("the session lists {count:d} subagents"))
def _the_session_lists_n_subagents(world: World, daemon: Daemon, count: int) -> None:
    def counted() -> list[ActorResponse] | None:
        listed = observe.subagents(daemon, str(world.session_id))
        return listed if len(listed) >= count else None

    observe.until(
        lambda: (
            f"{count} subagents; the session lists "
            f"{[(actor.actor_id, actor.name) for actor in observe.actors(daemon, str(world.session_id))]}"
        ),
        counted,
        timeout=FEED_SETTLE_TIMEOUT_SECONDS,
    )


@then(parsers.parse("every subagent finishes within {minutes:d} minutes"))
def _every_subagent_finishes(world: World, daemon: Daemon, minutes: int) -> None:
    def all_finished() -> list[ActorResponse] | None:
        listed = observe.subagents(daemon, str(world.session_id))
        return listed if listed and all(actor.state == "finished" for actor in listed) else None

    observe.until(
        lambda: (
            "every subagent to report its own end; they read "
            f"{[(actor.actor_id, actor.state) for actor in observe.subagents(daemon, str(world.session_id))]}"
        ),
        all_finished,
        timeout=minutes * 60,
    )


@then(parsers.parse("that assignment reported '{text}'"))
def _that_assignment_reported(world: World, daemon: Daemon, text: str) -> None:
    """The agent's RESULT, which is the half of a delegation the parent cannot
    know: it travels back in the same report that ends the assignment, and an end
    recorded without it leaves a finished agent whose answer nothing shows."""
    assert world.assignment is not None, "no agent assignment has been found yet"
    assignment_id = world.assignment.assignment_id

    def reported() -> observe.Assignment | None:
        for assignment in folded_assignments(world, daemon):
            if assignment.assignment_id == assignment_id and text in assignment.result:
                return assignment
        return None

    def missing() -> str:
        seen = [(item.assignment_id, item.result[:80])
                for item in folded_assignments(world, daemon)]
        return f"the assignment to report {text!r}; the session's assignments read {seen}"

    world.assignment = observe.until(missing, reported, timeout=FEED_SETTLE_TIMEOUT_SECONDS)
