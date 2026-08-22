"""What a scenario carries, and the reads every section makes.

The `World` is one scenario's mutable state: what was asked for, what it
started, and what the last step found so a following sentence can say "that
command" and mean this one. The readers below are shared because more than one
section asks the same questions — and because WHICH actor a question is about is
a decision this suite has to make consistently.
"""

from __future__ import annotations

from dataclasses import dataclass

from api.sessiondata.models.entry import EntryResponse
from api.sessiondata.models.session_data import ActorResponse, SessionResponse
from support import observe
from support.daemon import Daemon
from support.harness import Launched


@dataclass
class World:
    """One scenario's mutable state — what was asked for, and what it started."""

    harness: str = ""
    model: str | None = None
    effort: str | None = None
    prompt: str = ""
    session_id: str | None = None
    live: Launched | None = None
    # What the last step found, so a following sentence can say "that command" /
    # "that job" and mean this one rather than searching again and possibly
    # landing on something else. Three fields because they are three shapes: a
    # folded command, a folded delegation, and an actor.
    shell: observe.Shell | None = None
    assignment: observe.Assignment | None = None
    actor: ActorResponse | None = None
    # Which KIND of background work the last naming step found, so that "that
    # job …" and "that monitor …" keep meaning what their own sentence said. A
    # monitor recorded as an ordinary background job would otherwise satisfy
    # every following step.
    execution: str = "background"
    # WHOSE feed the feed-reading sentences are about. None is the whole
    # session's, which is what "the feed" means until a scenario says otherwise;
    # a subagent scenario points it at the actor it just found, and every
    # existing sentence about the feed then reads that actor's thread without
    # knowing it moved.
    viewpoint: str | None = None
    # The prompt total the next "turn ends" must observe. Without this fence a
    # second send can sample the previous turn's already-quiet status and race
    # ahead before the newly accepted prompt has even reached the transcript.
    expected_prompt_count: int | None = None


def feed(world: World, daemon: Daemon) -> tuple[EntryResponse, ...]:
    """The entries of whichever actor this scenario is currently looking at.

    An actor is ALWAYS named, and when a scenario has not said which, it is the
    LEAD. "The session's feed" has always meant the lead's thread — a subagent's
    work is not in it, and that is exactly what makes a feed read at one actor
    evidence about attribution rather than just about arrival. Reading every
    actor's entries here would make the absence this suite asserts unprovable.
    """
    actor_id = world.viewpoint or str(observe.lead(daemon, str(world.session_id)).actor_id)
    return observe.entries(daemon, str(world.session_id), actor_id)


def folded_shells(world: World, daemon: Daemon) -> list[observe.Shell]:
    """Every command in the current viewpoint's feed, each folded whole."""
    return observe.shells(feed(world, daemon))


def folded_assignments(world: World, daemon: Daemon) -> list[observe.Assignment]:
    """Every delegation in the session, each folded whole.

    Read across ALL actors, unlike the feed, because a delegation's two halves
    genuinely belong to two of them: the start is the parent's own tool call, and
    the end is the CHILD reporting its result from inside its own process. That is
    the whole reason the end can arrive after the parent has moved on — and it
    means an assignment scoped to one actor is half an assignment.
    """
    return observe.assignments(observe.entries(daemon, str(world.session_id)))


def background_work(world: World, daemon: Daemon) -> list[observe.Shell]:
    """The commands of the KIND this scenario is talking about.

    A monitor and a background job are both commands whose output outlives their
    turn, and the difference is the one thing a monitor is: `execution ==
    "monitor"` means a watch ARMED to report events until it is stopped. A
    command MOVED to the background mid-run is a background job even though it
    started as a foreground one, which is what `backgrounded` records.
    """
    if world.execution == "monitor":
        return [found for found in folded_shells(world, daemon) if found.execution == "monitor"]
    return [
        found for found in folded_shells(world, daemon)
        if found.execution == "background" or found.backgrounded
    ]


def diagnostics(daemon: Daemon, session_id: str | None) -> str:
    """What to look at when a wait ran out, from the API and the log only.

    This used to be a screenshot of the harness's terminal, which is gone with
    the passthrough. What replaced it is better for most failures anyway: the
    daemon's own log says whether it was even asked, and the session's last
    entries plus its actors' status say how far the evidence got. A screen tells
    you what a TUI was drawing; these tell you what the product understood.
    """
    lines = ["  daemon log tail:"]
    lines.extend("    " + row for row in daemon.log().rstrip().splitlines()[-12:] or ["(empty)"])
    if session_id is None:
        return "\n".join(lines)
    try:
        actors = [
            f"{actor.actor_id}={actor.status or 'no status'}"
            for actor in observe.actors(daemon, session_id)
        ]
        entries = [
            f"{entry.type}@{entry.cursor}"
            for entry in observe.entries(daemon, session_id)[-12:]
        ]
    except AssertionError as error:                 # the session may not exist yet
        return "\n".join([*lines, f"  read model: {error}"])
    return "\n".join([
        *lines,
        "  actors: " + (", ".join(actors) or "(none)"),
        "  last entries: " + (", ".join(entries) or "(none)"),
    ])


def session_facts(daemon: Daemon, world: World) -> SessionResponse:
    """What the session reports about ITSELF. Present by the time any `Then`
    asks: the sentences that call this run after the session has been found,
    which is the same thing as its first fact having been interpreted."""
    return observe.session(daemon, str(world.session_id))
