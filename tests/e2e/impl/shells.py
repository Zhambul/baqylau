"""Commands, and the two ways their output outlives the turn that started one.

A shell command, a command moved to the background, and a monitor — one watch
armed to report events until it stops. They share a section because they share a
FOLD: all three are several entries (a start, output chunks, a finish) that a
client assembles into one thing, and the assertions here are about that assembly
as much as about the work.
"""

from __future__ import annotations

from pytest_bdd import parsers, then, when

from impl.world import World, background_work, diagnostics, folded_shells
from support import observe
from support.daemon import Daemon

FEED_SETTLE_TIMEOUT_SECONDS = 60.0
# How long to wait for a harness to take the backgrounding gesture once its
# command is running: claude-code offers it 2 s in, and the margin is for the
# interpreter's own lag in telling us the command started at all.
BACKGROUND_OFFER_TIMEOUT_SECONDS = 60.0
SESSION_ANNOUNCE_TIMEOUT_SECONDS = 120.0


@when("I move that command to the background")
def _i_move_that_command_to_the_background(world: World, daemon: Daemon) -> None:
    """The background CONTROL, which is the whole point of this step's rewrite.

    It used to be a raw ctrl+b that this suite aimed at a terminal window, gated
    by a screen read for the harness's own "run in background" hint — because
    claude-code registers the chord's handler and prints that hint together, 2000
    ms into a foreground command, and a chord sent earlier lands in the composer
    where it is indistinguishable from one the harness ignored.

    That timing is the HARNESS's knowledge of its own screen, so it lives in the
    harness's controller now and every caller benefits: a browser click gets the
    same reliability this test does. What is left here is retrying the gesture,
    because the wait is bounded on the handler's side and the command may not be
    running yet — the handler's 409 says "not now", which is the honest answer to
    ask again about.
    """
    assert world.session_id is not None
    session_id = str(world.session_id)
    observe.until(
        lambda: "a foreground shell command to be running"
                + diagnostics(daemon, session_id),
        lambda: any(
            found.state is None and not found.backgrounded
            for found in folded_shells(world, daemon)
        ),
        timeout=SESSION_ANNOUNCE_TIMEOUT_SECONDS,
    )

    outcomes: list[str] = []

    def accepted() -> bool:
        status, body = daemon.post(
            f"/api/sessions/{session_id}/controls/background",
            {"request_id": "e2e-background-%d" % len(outcomes)},
        )
        outcomes.append(f"{status} {body[:120]}")
        return status == 200

    observe.until(
        lambda: "the harness to accept the background gesture; it answered "
                + " | ".join(outcomes[-3:]) + diagnostics(daemon, session_id),
        accepted,
        timeout=BACKGROUND_OFFER_TIMEOUT_SECONDS,
    )
    # An acknowledgement means the keystroke reached a program that was ready for
    # it. The ENTRY is the harness saying it acted — two different claims, and
    # only the second is evidence, so the scenario waits for it here rather than
    # inferring it three steps later from the command turning up in the jobs set.
    observe.until(
        lambda: "the shell_backgrounded entry for the command that was moved"
                + diagnostics(daemon, session_id),
        lambda: any(found.backgrounded for found in folded_shells(world, daemon)),
        timeout=FEED_SETTLE_TIMEOUT_SECONDS,
    )


@then(parsers.parse("the feed shows a {state} shell command '{command}'"))
def _the_feed_shows_a_shell_command(world: World, daemon: Daemon, state: str, command: str) -> None:
    """One command, folded from its own entries — a start, its output, a finish.

    The fold is the client's job now (support/observe.py `shells`), which is why
    this reads a `Shell` rather than a rendered block: the daemon serves the
    facts and every frontend, this suite included, assembles them the same way.
    """
    def found() -> observe.Shell | None:
        for shell in folded_shells(world, daemon):
            if shell.state == state and command in shell.command:
                return shell
        return None

    def missing() -> str:
        seen = [(shell.state, shell.command[:60]) for shell in folded_shells(world, daemon)]
        return f"a {state} shell command running {command!r}; the feed has {seen}"

    world.shell = observe.until(missing, found, timeout=FEED_SETTLE_TIMEOUT_SECONDS)


@then(parsers.parse("that command printed '{text}'"))
def _that_command_printed(world: World, daemon: Daemon, text: str) -> None:
    """The command's own output, embedded in its entries rather than fetched.

    Re-read rather than taken off `world.shell`: the finish and the last output
    chunk are separate facts, so a command can be `succeeded` a moment before the
    chunk that carries its last line has arrived.
    """
    assert world.shell is not None, "no shell command has been found yet"
    shell_id = world.shell.shell_id

    def printed() -> observe.Shell | None:
        for shell in folded_shells(world, daemon):
            if shell.shell_id == shell_id and text in shell.output:
                return shell
        return None

    def missing() -> str:
        current = [shell for shell in folded_shells(world, daemon) if shell.shell_id == shell_id]
        output = current[0].output if current else ""
        return f"the command to print {text!r}; its output reads {output[:200]!r}"

    world.shell = observe.until(missing, printed, timeout=FEED_SETTLE_TIMEOUT_SECONDS)


@then(parsers.parse("the session counts at least {count:d} shell command"))
def _the_session_counts_shell_commands(world: World, daemon: Daemon, count: int) -> None:
    # The scorebar's own counter, not a recount of the feed: it is written by its
    # own writer and it has its own way of being wrong.
    counted = observe.shell_command_count(daemon, str(world.session_id))
    assert counted >= count, f"the session counts {counted} shell commands, expected at least {count}"


@then(parsers.parse("the session lists a background job '{command}'"))
def _the_session_lists_a_background_job(world: World, daemon: Daemon, command: str) -> None:
    world.execution = "background"
    _find_background_work(world, daemon, command, "background job")


@then(parsers.parse("the session lists a monitor '{command}'"))
def _the_session_lists_a_monitor(world: World, daemon: Daemon, command: str) -> None:
    """A monitor, not a background job — the two are not the same fact.

    Claude Code's Monitor tool arms a watch: its command's every stdout line is
    delivered as an EVENT, and the watch runs until it exits, times out, or is
    stopped. Reaching this at all requires `execution == "monitor"` on the shell
    entry, which is the one thing about a monitor the translation has always got
    right, and the aggregate's own `monitor_count` has to agree — a monitor filed
    as an ordinary job would leave that counter at zero.
    """
    world.execution = "monitor"
    _find_background_work(world, daemon, command, "monitor")
    monitors, _jobs = observe.background_counts(daemon, str(world.session_id))
    assert monitors >= 1, "the session counts no monitors at all"


def _find_background_work(world: World, daemon: Daemon, command: str, noun: str) -> None:
    def found() -> observe.Shell | None:
        for shell in background_work(world, daemon):
            if command in shell.command:
                return shell
        return None

    def missing() -> str:
        seen = [
            (shell.command[:60], shell.execution, shell.backgrounded)
            for shell in folded_shells(world, daemon)
        ]
        return f"a {noun} running {command!r}; the feed's commands are {seen}"

    world.shell = observe.until(missing, found, timeout=FEED_SETTLE_TIMEOUT_SECONDS)


@then("that job is still running")
@then("that monitor is still running")
def _that_job_is_still_running(world: World, daemon: Daemon) -> None:
    """The claim the jobs panel exists to make, and the one it could not make.

    The aggregate's running set is the whole of it now: a command enters it when
    it is launched into the background or moved there, and LEAVES it when its
    own output ends — not when the tool call that launched it returned. That
    distinction is the bug this assertion was written for: `live` used to be the
    state of the launch, which returns at once, so every job read as already
    ended with the launch's time and the launch's outcome.

    Asserted BEFORE waiting for the output, so a job that is only ever seen
    after it finished cannot satisfy it by accident.
    """
    assert world.shell is not None, "no background work has been found yet"
    running = observe.running_shell_ids(daemon, str(world.session_id))
    assert world.shell.shell_id in running, (
        f"the work reads as already ended; the session's running set is {sorted(running)}"
    )


@then(parsers.parse("that job ends within {minutes:d} minutes"))
@then(parsers.parse("that monitor ends within {minutes:d} minutes"))
def _that_job_ends(world: World, daemon: Daemon, minutes: int) -> None:
    """…and the end is the WORK's own, announced separately from its launch.

    Read as leaving the running set rather than as a state on an entry, because
    that IS the fact: background work ends when its output ends, and nothing in
    the feed says so — the entry stream carries the output, and the aggregate
    carries whether more of it is coming.
    """
    assert world.shell is not None, "no background work has been found yet"
    shell_id = world.shell.shell_id
    observe.until(
        lambda: (
            f"the background work {shell_id!r} to report its own end; the session's "
            f"running set is {sorted(observe.running_shell_ids(daemon, str(world.session_id)))}"
        ),
        lambda: shell_id not in observe.running_shell_ids(daemon, str(world.session_id)),
        timeout=minutes * 60,
    )


@then(parsers.parse("that job prints '{text}' within {minutes:d} minutes"))
def _that_job_prints(world: World, daemon: Daemon, text: str, minutes: int) -> None:
    """The job's OUTPUT, which is the whole path this scenario exists for: a fact
    produced after its turn was over — discovered output file, followed, appended
    as an output chunk, folded back into one command by the client."""
    assert world.shell is not None, "no background job has been found yet"
    shell_id = world.shell.shell_id

    def printed() -> observe.Shell | None:
        for shell in background_work(world, daemon):
            if shell.shell_id == shell_id and text in shell.output:
                return shell
        return None

    def missing() -> str:
        seen = [(shell.command[:40], shell.output[:80]) for shell in background_work(world, daemon)]
        return f"the background job to print {text!r}; the jobs read {seen}"

    world.shell = observe.until(missing, printed, timeout=minutes * 60)


@then(parsers.parse("that monitor reports the event '{text}' within {minutes:d} minutes"))
def _that_monitor_reports_the_event(world: World, daemon: Daemon, text: str, minutes: int) -> None:
    """A monitor's EVENTS — the one thing a monitor has that a background job does not.

    Asserted on the STATUS stream rather than on the output, because that is
    where a monitor's ticks arrive and the two have different sources: a job's
    output is a FILE the interpreter follows, while a monitor's events are
    delivered to the harness as notifications. Nothing about a working jobs panel
    implies this works.

    And asserted on the SECOND tick, not the first. The fixture prints a tick and
    then sleeps, so tick-1 leaves the command at the instant the monitor arms —
    simultaneously with the harness reporting "Monitor started" — and whether it
    is delivered as its own notification or folded into that start is a race
    nobody promises either way. Measured: tick-1 arrived as its own status entry
    on two runs and never appeared as one on a third. tick-2 is a whole sleep
    later, so it cannot collide with the arming, and it proves exactly what this
    scenario is for: an event reaching us after the turn that armed the watch had
    already ended.
    """
    assert world.shell is not None, "no monitor has been found yet"
    shell_id = world.shell.shell_id

    def reported() -> observe.Shell | None:
        for shell in background_work(world, daemon):
            if shell.shell_id == shell_id and text in shell.status:
                return shell
        return None

    def missing() -> str:
        seen = [(shell.command[:40], shell.status[:80]) for shell in background_work(world, daemon)]
        return f"the monitor to report an event containing {text!r}; the monitors read {seen}"

    world.shell = observe.until(missing, reported, timeout=minutes * 60)


@then(parsers.parse("the feed shows no shell command '{command}'"))
def _the_feed_shows_no_shell_command(world: World, daemon: Daemon, command: str) -> None:
    """An absence, and only ever asserted about a fact already SEEN somewhere
    else — otherwise it would pass for the one reason that proves nothing, that
    the command has not arrived yet."""
    ran = [shell.command for shell in folded_shells(world, daemon)]
    assert not any(command in text for text in ran), (
        f"this feed shows the shell command {command!r}, which belongs to another actor: {ran}"
    )
