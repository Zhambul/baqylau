"""The live-harness suite: its fixtures, and every sentence a feature may say.

This suite is not hermetic and does not pretend to be. It starts the REAL
daemon, runs the REAL harness CLI against a real workspace on disk, and spends
real tokens — because the failure it exists to catch is a harness release
changing its evidence under an integration that keeps reporting success. Nothing
simulated can catch that.

What it does isolate is our own state: a private data directory (both databases)
and a private port, so a run never touches the developer's daemon on 8377. The
harness's OWN configuration — credentials, installed hooks — is deliberately the
real one (see support/environment.py).

Step definitions live here rather than in a steps/ package so that every feature
sees them without import gymnastics, and they stay one-liners over support/: a
sentence in a .feature file should read as intent, and the mechanics of a
pseudo-terminal belong somewhere a reader of the scenario never has to look.

The one thing NOT expressible as a sentence is the invariant every scenario is
held to — that nothing the harness said went uninterpreted. It is a fixture, not
a `Then`, precisely because a forgotten assertion is the failure mode this suite
exists to remove.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from pytest_bdd import given, parsers, then, when

from api.dashboard.models.sessions.activity_item import ActivityItemResponse
from api.dashboard.models.sessions.actor_summary import ActorSummaryResponse
from api.dashboard.models.sessions.background_work import BackgroundOperationResponse
from api.dashboard.models.sessions.session_summary import SessionSummaryResponse
from support import harness as harness_support
from support import observe
from support.daemon import Daemon, free_port, start
from support.harness import LiveHarness

REPOSITORY_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_WORKSPACE = os.path.expanduser("~/code/personal/baqylau-tests")

SESSION_ANNOUNCE_TIMEOUT_SECONDS = 120.0
INTERPRETER_DRAIN_TIMEOUT_SECONDS = 30.0
# How long a fact may take to travel from the harness's file to the dashboard's
# feed after the turn is already over: one interpreter poll plus slack.
FEED_SETTLE_TIMEOUT_SECONDS = 60.0
# How long to wait for a harness to offer backgrounding once its command is running:
# claude-code offers it 2 s in, and the margin is for the interpreter's own lag in
# telling us the command started at all.
BACKGROUND_OFFER_TIMEOUT_SECONDS = 60.0
# A turn is over when the session's own projection says the tab has gone quiet.
# Background work is a separate scenario's problem; either state means the
# harness has stopped answering.
TURN_ENDED = ("awaiting_response", "awaiting_background")


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("baqylau live-harness tests")
    group.addoption(
        "--e2e-workspace",
        default=DEFAULT_WORKSPACE,
        help="the directory the harness runs in (default: %(default)s)",
    )
    group.addoption(
        "--e2e-data-dir",
        default=None,
        help="keep the run's databases here instead of a tmpdir — what you want after a failure",
    )
    # The two overrides that let a new model be tried against the WHOLE suite
    # without editing a single Examples table.
    group.addoption("--e2e-model", default=None, help="override every scenario's model")
    group.addoption("--e2e-effort", default=None, help="override every scenario's effort")


@dataclass
class World:
    """One scenario's mutable state — what was asked for, and what it started."""

    harness: str = ""
    model: str | None = None
    effort: str | None = None
    prompt: str = ""
    session_id: str | None = None
    live: LiveHarness | None = None
    # What the last step found, so a following sentence can say "that command" /
    # "that job" and mean this one rather than searching again and possibly
    # landing on something else. Two fields because they are different shapes: a
    # rendered feed item, and a background-work row.
    subject: ActivityItemResponse | None = None
    job: BackgroundOperationResponse | None = None
    # WHICH of the snapshot's two background-work lists the last naming step
    # found `job` in, so that "that job …" and "that monitor …" keep looking in
    # the list their own sentence named. A monitor that starts being filed as a
    # job would otherwise still satisfy every following step.
    listing: str = "jobs"
    # WHOSE feed the feed-reading sentences are about. None is the lead's, which
    # is what "the feed" means until a scenario says otherwise; a subagent
    # scenario points it at the actor it just found, and every existing sentence
    # about the feed then reads that actor's thread without knowing it moved.
    viewpoint: str | None = None
    actor: ActorSummaryResponse | None = None


@pytest.fixture(scope="session")
def workspace(pytestconfig: pytest.Config) -> str:
    """The directory the harness works in. A real git repository, because a
    harness behaves differently outside one and the dashboard reads its status."""
    directory = os.path.abspath(os.path.expanduser(str(pytestconfig.getoption("--e2e-workspace"))))
    if not os.path.isdir(directory):
        raise pytest.UsageError(f"workspace does not exist: {directory}")
    return os.path.realpath(directory)


@pytest.fixture(scope="session")
def daemon(pytestconfig: pytest.Config, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Daemon]:
    """One daemon for the whole run, on its own port and its own databases."""
    configured = pytestconfig.getoption("--e2e-data-dir")
    data_directory = (
        os.path.abspath(os.path.expanduser(str(configured)))
        if configured
        else str(tmp_path_factory.mktemp("baqylau-live-data"))
    )
    running = start(REPOSITORY_ROOT, data_directory, free_port())
    print(f"\nlive daemon · {running.url} · data {data_directory}")
    try:
        yield running
    finally:
        running.stop()


@pytest.fixture
def world() -> World:
    return World()


@pytest.fixture(autouse=True)
def nothing_went_uninterpreted(world: World, daemon: Daemon) -> Iterator[None]:
    """After every scenario: the harness said nothing we failed to understand.

    Checked before the CLI is stopped (its exit writes evidence of its own) and
    after the interpreter has drained, so the verdict is complete rather than
    merely early. `ignored_nonsemantic` is not a finding — that is the
    interpreter recognising a record and having nothing to say about it.
    """
    yield
    try:
        if world.session_id is None:
            return
        observe.until(
            "the interpreter to rule on every raw event",
            lambda: observe.unverdicted_count(daemon) == 0,
            timeout=INTERPRETER_DRAIN_TIMEOUT_SECONDS,
        )
        unknown = observe.uninterpreted(daemon, world.session_id)
        errors = observe.audit_errors(daemon, world.session_id)
        assert not unknown, "the harness said things we did not understand:\n" + "\n".join(unknown)
        assert not errors, "the machinery recorded errors:\n" + "\n".join(errors)
    finally:
        if world.live is not None:
            world.live.stop()


def _feed(world: World, daemon: Daemon) -> tuple[ActivityItemResponse, ...]:
    """The activity of whichever actor this scenario is currently looking at."""
    return observe.feed(daemon, str(world.session_id), world.viewpoint)


def _listed(world: World, daemon: Daemon) -> tuple[BackgroundOperationResponse, ...]:
    """The background-work rows of whichever list this scenario is talking about."""
    reader = observe.monitors if world.listing == "monitors" else observe.background_jobs
    return reader(daemon, str(world.session_id))


def _session(daemon: Daemon, world: World) -> SessionSummaryResponse:
    """What the session reports about ITSELF. Present by the time any `Then`
    asks: the sentences that call this run after the session has been found,
    which is the same thing as its first fact having been interpreted."""
    summary = observe.session(daemon, str(world.session_id))
    assert summary is not None, f"session {world.session_id} has no summary yet"
    return summary


# --- the sentences -----------------------------------------------------------


@given(parsers.parse("a {harness} session on {model} at {effort} effort"))
def _a_session(world: World, pytestconfig: pytest.Config, harness: str, model: str, effort: str) -> None:
    """Records the intent only: the CLI starts when there is a prompt to give it,
    because the harnesses that announce themselves at all announce themselves on
    their first turn."""
    world.harness = harness
    world.model = str(pytestconfig.getoption("--e2e-model") or model)
    world.effort = str(pytestconfig.getoption("--e2e-effort") or effort)


@when(parsers.parse("I ask '{prompt}'"))
def _i_ask(world: World, daemon: Daemon, workspace: str, prompt: str) -> None:
    world.prompt = prompt
    # Taken BEFORE the launch: the session this scenario is about is the one
    # that was not there a moment ago (see observe.session_started_in).
    known = observe.session_ids(daemon)
    world.live = harness_support.launch(
        world.harness,
        workspace=workspace,
        prompt=prompt,
        model=world.model,
        effort=world.effort,
        port=daemon.port,
    )
    live = world.live

    def announced() -> str | None:
        # The trust gate is answered from inside the wait, not before it — see
        # LiveHarness.answer_first_run_gate.
        gate = live.answer_first_run_gate()
        if gate is not None:
            print(f"answered the workspace-trust prompt ({gate})")
        return observe.session_started_in(daemon, world.harness, workspace, known)

    world.session_id = observe.until(
        lambda: (
            f"{world.harness} to announce a session in {workspace}\n"
            f"  launched: {' '.join(live.command)}\n"
            f"  still running: {live.running()}\n"
            f"  screen:\n{live.screen()}"
        ),
        announced,
        timeout=SESSION_ANNOUNCE_TIMEOUT_SECONDS,
    )


@when(parsers.parse("I press {chord} once backgrounding is offered"))
def _i_press_a_chord_once_offered(world: World, daemon: Daemon, chord: str) -> None:
    """Waits for the harness to OFFER the gesture, then presses it.

    Not merely for the command to be running: claude-code 2.1.233 registers the
    handler and prints its "run in background" hint only once the command has been
    running for 2000 ms (the `detectBlockedSleepPattern` neighbourhood — measured in
    the binary). A chord sent before that lands in the composer and is
    indistinguishable from a gesture the harness ignored, which is exactly how
    three earlier attempts read.
    """
    assert world.live is not None, "no harness is running"
    live = world.live
    observe.until(
        lambda: f"a shell command to be running before {chord}\n  screen:\n{live.screen()}",
        lambda: any(
            item.state == "running"
            for item in observe.shell_operations(_feed(world, daemon))
        ),
        timeout=SESSION_ANNOUNCE_TIMEOUT_SECONDS,
    )
    observe.until(
        lambda: f"the harness to offer backgrounding\n  screen:\n{live.screen()}",
        lambda: harness_support.OFFER_MARKER in harness_support.flattened(live.screen()),
        timeout=BACKGROUND_OFFER_TIMEOUT_SECONDS,
    )
    live.send_chord(chord)


@then(parsers.parse("the turn ends within {minutes:d} minutes"))
def _the_turn_ends(world: World, daemon: Daemon, minutes: int) -> None:
    assert world.session_id is not None
    observe.until(
        f"the turn to end (tab state one of {TURN_ENDED})",
        lambda: observe.tab_state(daemon, str(world.session_id)) in TURN_ENDED,
        timeout=minutes * 60,
    )


@then(parsers.parse("the session reports the model {model}"))
def _reports_the_model(world: World, daemon: Daemon, model: str) -> None:
    # `world.model`, not the step's `model`: --e2e-model may have replaced what
    # the Examples table asked for, and the assertion is about what was LAUNCHED.
    reported = _session(daemon, world).model
    assert observe.model_matches(reported, str(world.model)), (
        f"launched with model {world.model!r}, session reports {reported!r}"
    )


@then(parsers.parse("the session reports {effort} effort"))
def _reports_the_effort(world: World, daemon: Daemon, effort: str) -> None:
    reported = _session(daemon, world).effort
    assert reported == world.effort, f"launched at effort {world.effort!r}, session reports {reported!r}"


@then(parsers.parse("the feed shows my prompt '{text}'"))
def _the_feed_shows_my_prompt(world: World, daemon: Daemon, text: str) -> None:
    observe.until(
        lambda: (
            f"a prompt bubble reading {text!r}; the feed has "
            f"{[item.plain_text[:80] for item in observe.prompts(_feed(world, daemon))]}"
        ),
        lambda: any(
            item.plain_text.strip() == text
            for item in observe.prompts(_feed(world, daemon))
        ),
        timeout=FEED_SETTLE_TIMEOUT_SECONDS,
    )


@then(parsers.parse("the feed shows a {state} shell command '{command}'"))
def _the_feed_shows_a_shell_command(world: World, daemon: Daemon, state: str, command: str) -> None:
    def found() -> ActivityItemResponse | None:
        for item in observe.shell_operations(_feed(world, daemon)):
            if item.state == state and command in observe.operation_command(daemon, item):
                return item
        return None

    def missing() -> str:
        blocks = [
            (item.state, observe.operation_command(daemon, item)[:60])
            for item in observe.shell_operations(_feed(world, daemon))
        ]
        return f"a {state} shell block running {command!r}; the feed has {blocks}"

    world.subject = observe.until(missing, found, timeout=FEED_SETTLE_TIMEOUT_SECONDS)


@then(parsers.parse("that command printed '{text}'"))
def _that_command_printed(world: World, daemon: Daemon, text: str) -> None:
    assert world.subject is not None, "no shell block has been found yet"
    output = observe.operation_output(daemon, world.subject)
    assert text in output, f"the command's output reads {output[:200]!r}, expected it to contain {text!r}"


@then(parsers.parse("the session counts at least {count:d} shell command"))
def _the_session_counts_shell_commands(world: World, daemon: Daemon, count: int) -> None:
    # The scorebar's own counter, not a recount of the feed: it is a separate
    # projection and it has its own way of being wrong.
    counted = observe.statistics(daemon, str(world.session_id)).shell_command_count
    assert counted >= count, f"the session counts {counted} shell commands, expected at least {count}"


@then(parsers.parse("the session lists a background job '{command}'"))
def _the_session_lists_a_background_job(world: World, daemon: Daemon, command: str) -> None:
    world.listing = "jobs"
    _find_background_work(world, daemon, command, "background job")


@then(parsers.parse("the session lists a monitor '{command}'"))
def _the_session_lists_a_monitor(world: World, daemon: Daemon, command: str) -> None:
    """The monitors list, not the jobs list — a monitor is not a background job.

    Claude Code's Monitor tool arms a watch: its command's every stdout line is
    delivered as an EVENT, and the watch runs until it exits, times out, or is
    stopped. Reaching the monitors list at all requires
    `OperationStarted(execution="monitor")`, which is the one thing about a
    monitor the translation has always got right.
    """
    world.listing = "monitors"
    _find_background_work(world, daemon, command, "monitor")


def _find_background_work(world: World, daemon: Daemon, command: str, noun: str) -> None:
    def found() -> BackgroundOperationResponse | None:
        for row in _listed(world, daemon):
            if command in row.command:
                return row
        return None

    def missing() -> str:
        listed = [(row.command[:60], row.live) for row in _listed(world, daemon)]
        return f"a {noun} running {command!r}; the session lists {listed}"

    world.job = observe.until(missing, found, timeout=FEED_SETTLE_TIMEOUT_SECONDS)


@then("that job is still running")
@then("that monitor is still running")
def _that_job_is_still_running(world: World, daemon: Daemon) -> None:
    """The claim the jobs tab exists to make, and the one it could not make.

    `live` used to be the state of the tool call that LAUNCHED the job, which
    returns at once — so every job read as already ended, with the launch's time
    and the launch's outcome. Asserted BEFORE waiting for the output so a job that
    is only ever seen after it finished cannot satisfy it by accident.
    """
    assert world.job is not None, "no background job has been found yet"
    command = str(world.job.command)
    live = [row for row in _listed(world, daemon) if row.command == command]
    assert live and live[0].live is True, f"the job reads as already ended: {live}"
    assert live[0].ended_at is None, f"the job carries an end time while running: {live[0].ended_at}"


@then(parsers.parse("that job ends within {minutes:d} minutes"))
@then(parsers.parse("that monitor ends within {minutes:d} minutes"))
def _that_job_ends(world: World, daemon: Daemon, minutes: int) -> None:
    """…and the end is the WORK's own, announced separately from its launch."""
    assert world.job is not None, "no background job has been found yet"
    command = str(world.job.command)

    def ended() -> BackgroundOperationResponse | None:
        for row in _listed(world, daemon):
            if row.command == command and not row.live:
                return row
        return None

    job = observe.until(f"the background work {command!r} to report its own end", ended, timeout=minutes * 60)
    assert job.ended_at is not None
    assert job.end_reason == "succeeded", f"the job ended {job.end_reason!r}"


@then(parsers.parse("that job prints '{text}' within {minutes:d} minutes"))
def _that_job_prints(world: World, daemon: Daemon, text: str, minutes: int) -> None:
    """The job's OUTPUT is the claim, and it is deliberately not its liveness.

    `live` on a background job is `operation.state == "running"`
    (dashboard/services/sessions.py) — the state of the tool call that LAUNCHED it,
    which returns at once. So a job is never live, `ended_at` is its launch time,
    and a step waiting for one to "finish" passes the instant it is asked
    (measured: a `sleep 5 && echo done` reported ended_at 1.07s after started_at).
    The job's true end is `OperationOutputFinished`, which the snapshot does not
    carry, so there is nothing honest to assert about it from here.

    What the output DOES prove is the whole path this scenario exists for: a fact
    produced after its turn was over — discovered output file, followed, appended
    as progress, projected into the jobs tab.
    """
    assert world.job is not None, "no background job has been found yet"
    command = str(world.job.command)

    def printed() -> BackgroundOperationResponse | None:
        for job in observe.background_jobs(daemon, str(world.session_id)):
            if job.command == command and text in str(job.output):
                return job
        return None

    def missing() -> str:
        outputs = [
            (job.command[:40], str(job.output)[:80])
            for job in observe.background_jobs(daemon, str(world.session_id))
        ]
        return f"the background job {command!r} to print {text!r}; jobs read {outputs}"

    world.job = observe.until(missing, printed, timeout=minutes * 60)


@then(parsers.parse("that monitor reports the event '{text}' within {minutes:d} minutes"))
def _that_monitor_reports_the_event(world: World, daemon: Daemon, text: str, minutes: int) -> None:
    """A monitor's EVENTS — the one thing a monitor has that a background job does not.

    Asserted on the `events` list rather than on the joined `output` because that
    is the shape the monitors tab draws (one row per event), and because the two
    have different sources: a job's output is a FILE the interpreter follows,
    while a monitor's events are delivered to the harness's transcript as
    notifications. Nothing about a working jobs tab implies this works.
    """
    assert world.job is not None, "no monitor has been found yet"
    command = str(world.job.command)

    def reported() -> BackgroundOperationResponse | None:
        for row in _listed(world, daemon):
            if row.command == command and any(text in str(e.event) for e in row.events):
                return row
        return None

    def missing() -> str:
        seen = [
            (row.command[:40], [str(e.event)[:40] for e in row.events])
            for row in _listed(world, daemon)
        ]
        return f"the monitor {command!r} to report an event containing {text!r}; monitors read {seen}"

    world.job = observe.until(missing, reported, timeout=minutes * 60)


@then(parsers.parse("the session lists a subagent '{name}'"))
def _the_session_lists_a_subagent(world: World, daemon: Daemon, name: str) -> None:
    """A second ACTOR in the session, launched by the first.

    The actor, not the assignment: those are two facts from two sources, and only
    together do they mean a subagent ran. The assignment is what the lead's own
    tool call said it wanted (`Task` → `actor.assignment_started`); the actor is
    the child announcing itself from inside its own process (Claude Code's
    SubagentStart hook → `actor.started`). A session showing one without the
    other is a session where the delegation was recorded and the delegate was
    never seen, or the reverse.
    """
    def found() -> ActorSummaryResponse | None:
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

    def finished() -> ActorSummaryResponse | None:
        for actor in observe.subagents(daemon, str(world.session_id)):
            if str(actor.actor_id) == actor_id and actor.state == "finished":
                return actor
        return None

    actor = observe.until(f"the subagent {actor_id!r} to report its own end", finished,
                          timeout=minutes * 60)
    assert actor.finished_at is not None, "the subagent finished without an end time"


@when("I look at that subagent")
def _i_look_at_that_subagent(world: World) -> None:
    """Switches the viewpoint, which is a real request the dashboard makes
    (`/activity?actor_id=…`) and not a filter this suite invented. Every sentence
    about "the feed" from here on is about the subagent's own thread."""
    assert world.actor is not None, "no subagent has been found yet"
    world.viewpoint = str(world.actor.actor_id)


@when("I look at the session itself")
def _i_look_at_the_session_itself(world: World) -> None:
    world.viewpoint = None


@then(parsers.parse("the feed shows a {state} agent assignment"))
def _the_feed_shows_an_agent_assignment(world: World, daemon: Daemon, state: str) -> None:
    """The delegation as the lead's feed draws it — a block whose outcome is the
    AGENT's, arriving long after the tool call that started it returned."""
    def found() -> ActivityItemResponse | None:
        for item in observe.assignments(_feed(world, daemon)):
            if item.state == state:
                return item
        return None

    def missing() -> str:
        seen = [(item.state, item.actor_assignment_phase)
                for item in observe.assignments(_feed(world, daemon))]
        return f"a {state} agent assignment; the feed has {seen}"

    world.subject = observe.until(missing, found, timeout=FEED_SETTLE_TIMEOUT_SECONDS)
    phase = "started" if state == "running" else "finished"
    assert world.subject.actor_assignment_phase == phase, (
        f"a {state} assignment is shown in phase "
        f"{world.subject.actor_assignment_phase!r}, expected {phase!r}"
    )


@then(parsers.parse("the feed shows {count:d} succeeded agent assignments"))
def _the_feed_shows_n_assignments(world: World, daemon: Daemon, count: int) -> None:
    """Two delegations from one turn have to stay two.

    A count, because the failure this catches is not a missing event but a
    COLLAPSED one: canonical identity is derived from the subject's id
    (`stable_event_id`), so two assignments that end up sharing one — an id
    defaulted to the empty string, say — become a single row, and every other
    assertion about an assignment still passes.
    """
    def counted() -> list[ActivityItemResponse] | None:
        succeeded = [item for item in observe.assignments(_feed(world, daemon))
                     if item.state == "succeeded"]
        return succeeded if len(succeeded) >= count else None

    def missing() -> str:
        seen = [(item.state, item.actor_assignment_id)
                for item in observe.assignments(_feed(world, daemon))]
        return f"{count} succeeded agent assignments; the feed has {seen}"

    found = observe.until(missing, counted, timeout=FEED_SETTLE_TIMEOUT_SECONDS)
    identities = {item.actor_assignment_id for item in found}
    assert len(identities) >= count, f"{len(found)} assignment rows share {identities}"


@then(parsers.parse("the session lists {count:d} subagents"))
def _the_session_lists_n_subagents(world: World, daemon: Daemon, count: int) -> None:
    def counted() -> list[ActorSummaryResponse] | None:
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
    def all_finished() -> list[ActorSummaryResponse] | None:
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
    know: it travels back in the same `<task-notification>` that ends the
    assignment, and an end recorded without it leaves a finished agent whose
    answer the dashboard never shows."""
    assert world.subject is not None, "no agent assignment has been found yet"
    reported = observe.item_text(daemon, world.subject)
    assert text in reported, (
        f"the assignment reports {reported[:200]!r}, expected it to contain {text!r}"
    )


@then(parsers.parse("the feed shows no shell command '{command}'"))
def _the_feed_shows_no_shell_command(world: World, daemon: Daemon, command: str) -> None:
    """An absence, and only ever asserted about a fact already SEEN somewhere
    else — otherwise it would pass for the one reason that proves nothing, that
    the command has not arrived yet."""
    ran = [
        observe.operation_command(daemon, item)
        for item in observe.shell_operations(_feed(world, daemon))
    ]
    assert not any(command in text for text in ran), (
        f"this feed shows the shell command {command!r}, which belongs to another actor: {ran}"
    )


@then(parsers.parse("the assistant ends the turn with '{text}'"))
def _the_assistant_ends_the_turn_with(world: World, daemon: Daemon, text: str) -> None:
    # Waited for, not sampled: the turn ends on a HOOK, which is instant, while
    # the answer itself arrives in the TRANSCRIPT, which is polled. Asserting the
    # moment the turn ends would fail on the lag between the two — and would pass
    # or fail depending on the machine, which is worse than failing.
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
    def ended_on_it() -> list[ActivityItemResponse] | None:
        items = _feed(world, daemon)
        answers = observe.assistant_messages(items)
        if not answers or answers[-1].plain_text.strip() != text:
            return None
        enders = observe.turn_enders(items)
        return answers if enders and enders[-1].message_id == answers[-1].message_id else None

    def missing() -> str:
        bubbles = [
            (item.plain_text[:40], item.final)
            for item in observe.assistant_messages(_feed(world, daemon))
        ]
        return (
            f"the assistant to end the turn on {text!r} and the feed to mark that "
            f"message as final; the bubbles read {bubbles}"
        )

    observe.until(missing, ended_on_it, timeout=FEED_SETTLE_TIMEOUT_SECONDS)
