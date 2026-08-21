"""What the dashboard says, read back the way the browser reads it.

Every assertion in this suite goes through `/sessionData` — the same two
resources the page is built from, the aggregate and the feed. There is no
rendered item any more: the daemon serves facts and each frontend draws them, so
what this suite asserts on is the fact, and the DOM those facts become is a
later Playwright tier's business.

Read back into the ROUTES' OWN RESPONSE MODELS, never into dicts. Every reader
below returns a real type, so `entry.body.state` is checked at the point it is
written and a renamed field breaks the rig at the read rather than at a timeout
twenty lines later — which is what `item.get("state")` on an untyped dict bought
us: None, no error, and a scenario waiting out its clock for a state nobody was
going to send.

Two things follow from the read model that this file is shaped by. Content is
EMBEDDED, so a command's text and its output are in the entry and there is no
second request to resolve them. And a command is several entries — a start, its
output chunks, a finish — so the fold that makes them one thing is here, exactly
as it is in the browser and in the pane: `shell()` below is this suite's copy of
the same rule, and it is short because the rule is.

The two verdict readers at the bottom have no route because nothing in the
product asks for them over HTTP: they read `main.db` / `audit.db` through the
repositories that own those tables, which is also how
`bin/baqylau-raw-events-audit.py` reads them.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import TypeAlias, TypeVar

from pydantic import TypeAdapter

from api.sessiondata.models.entry import (
    AssignmentFinishedBodyResponse,
    AssignmentStartedBodyResponse,
    EntryPageResponse,
    EntryResponse,
    MessageBodyResponse,
    ShellBackgroundedBodyResponse,
    ShellFinishedBodyResponse,
    ShellOutputBodyResponse,
    ShellStartedBodyResponse,
)
from api.sessiondata.models.session_data import (
    ActorResponse,
    SessionDataListResponse,
    SessionDataResponse,
    SessionResponse,
)
from domain.ids import SessionId
from repository.impl.sqlite.audit import SqliteAuditReadRepository
from repository.impl.sqlite.databases import audit_database, main_database, read_only
from repository.impl.sqlite.raw_event_audits import SqliteRawEventAuditRepository
from repository.impl.sqlite.raw_events import SqliteRawEventRepository
from support.daemon import Daemon

T = TypeVar("T")

POLL_SECONDS = 0.5
# One page holds a whole scenario's feed. Deliberately the route's maximum: a
# scenario that needed paging would be asserting on a window rather than on a
# session, and the assertion that a command is ABSENT (the subagent attribution
# case) is only honest over the whole feed.
ENTRY_LIMIT = 1000

# One adapter per resource this suite reads: the model the route declares.
SESSION_DATA_LIST = TypeAdapter(SessionDataListResponse)
SESSION_DATA = TypeAdapter(SessionDataResponse)
ENTRY_PAGE = TypeAdapter(EntryPageResponse)

# The verdicts that mean the interpreter did NOT understand what a harness said.
# `ignored_nonsemantic` is not one of them: that is the interpreter recognising a
# record and having nothing to say about it.
UNINTERPRETED = ("ignored_unknown", "translation_failed")

Entries: TypeAlias = Sequence[EntryResponse]


def until(description: str | Callable[[], str], read: Callable[[], T | None], timeout: float) -> T:
    """Poll `read` until it answers with something truthy; fail naming what was
    waited for. Every wait in this suite is one of these — a `sleep` long enough
    for a real model would make the suite unusable and still be a race.

    `description` may be a callable, and for anything that quotes the harness's
    screen it must be: built eagerly it reports the screen as it looked before
    the wait began, which is empty, which is the least useful thing it could say.
    """
    deadline = time.monotonic() + timeout
    while True:
        found = read()
        if found:
            return found
        if time.monotonic() >= deadline:
            said = description() if callable(description) else description
            raise AssertionError(f"timed out after {timeout:.0f}s waiting for {said}")
        time.sleep(POLL_SECONDS)


# --- the aggregate -----------------------------------------------------------


def session_ids(daemon: Daemon) -> frozenset[str]:
    """Every session the daemon knows about — the "before" of a launch."""
    return frozenset(
        data.session.session_id
        for data in daemon.read("/sessionData", SESSION_DATA_LIST).sessions
    )


def session_started_in(
    daemon: Daemon,
    harness: str,
    workspace: str,
    known: frozenset[str],
) -> str | None:
    """The id of the session that appeared since `known` was taken.

    Identified by being NEW rather than by being recent, because a start time is
    not evidence of whose session it is: with the daemon's own clock skew
    allowed for, a scenario starting seconds after the previous one ended could
    bind to that one instead — and it did, silently, until a faster rig made the
    gap small enough to hit (a scenario spent three minutes waiting for a turn
    to end in a session that had already finished). The launch response cannot
    settle it either: the harness, not the launcher, chooses the session id and
    announces it only in its own first evidence.

    Matched on the working directory the session reports NOW. The read model
    keeps one directory per session and follows the harness if it moves, so a
    scenario that changed directory mid-run would not be found this way — none
    does, and the alternative was a second stored field nothing else wanted.
    """
    appeared = [
        data.session
        for data in daemon.read("/sessionData", SESSION_DATA_LIST).sessions
        if data.session.session_id not in known
        and data.session.harness == harness
        and data.session.working_directory == workspace
    ]
    if not appeared:
        return None
    return max(appeared, key=lambda found: found.started_at or 0.0).session_id


def session_data(daemon: Daemon, session_id: str) -> SessionDataResponse:
    """The whole aggregate, as both frontends receive it."""
    return daemon.read(f"/sessionData/{session_id}", SESSION_DATA)


def session(daemon: Daemon, session_id: str) -> SessionResponse:
    """What the session IS. The route 400s until the session exists at all, so
    every caller of this has already waited for it to be announced."""
    return session_data(daemon, session_id).session


def actors(daemon: Daemon, session_id: str) -> tuple[ActorResponse, ...]:
    """Who is working in this session — the lead and every subagent under it, as
    the actor switcher lists them."""
    return session_data(daemon, session_id).actors


def lead(daemon: Daemon, session_id: str) -> ActorResponse:
    """The actor the session's own facts name as its lead.

    Everything the old snapshot said about "the session" — its model, its
    effort, its status, its counters — is an ACTOR's fact now, and for a session
    with subagents the one that answers those questions is the lead.
    """
    data = session_data(daemon, session_id)
    found = [
        actor for actor in data.actors
        if actor.actor_id == data.session.lead_actor_id
    ]
    assert found, f"session {session_id} has no row for its own lead actor"
    return found[0]


def status(daemon: Daemon, session_id: str) -> str | None:
    """The lead's liveness verdict — what paints the tab colour and the red/green
    alerts. Asked instead of scanning for a turn-finished entry so that "the turn
    ended" means here exactly what it means to the product."""
    return lead(daemon, session_id).status


def subagents(daemon: Daemon, session_id: str) -> list[ActorResponse]:
    """Every actor that is not the lead. Identified by HAVING A PARENT rather
    than by its role: `child` and `teammate` are two kinds of subagent and a
    harness may add a third, while an actor nobody launched is the lead."""
    return [actor for actor in actors(daemon, session_id) if actor.parent_actor_id]


def shell_command_count(daemon: Daemon, session_id: str) -> int:
    """The scorebar's own counter, summed across the actors that earned it.

    Per-actor in the read model because that is where a harness reports it; the
    scorebar's number is the session's, and the session's is the sum. Not a
    recount of the feed: it is written by its own writer and it has its own way
    of being wrong.
    """
    return sum(actor.statistics.shell_command_count for actor in actors(daemon, session_id))


def running_shell_ids(daemon: Daemon, session_id: str) -> frozenset[str]:
    """Every command the aggregate still counts as running, across all actors.

    This is what the jobs and monitors panels are rebuilt from. It is an id set
    and nothing more — no command text, no output, no end reason — which is the
    change: what a job IS lives in its entries, and only whether it is still
    going lives here.
    """
    return frozenset(
        shell_id
        for actor in actors(daemon, session_id)
        for shell_id in actor.background.running_shell_ids
    )


def background_counts(daemon: Daemon, session_id: str) -> tuple[int, int]:
    """How many monitors and how many background jobs this session has started,
    ever — the two counters the panels' headings show."""
    rows = actors(daemon, session_id)
    return (
        sum(actor.background.monitor_count for actor in rows),
        sum(actor.background.background_job_count for actor in rows),
    )


# --- the feed ----------------------------------------------------------------


def entries(daemon: Daemon, session_id: str, actor_id: str | None = None) -> tuple[
    EntryResponse, ...
]:
    """The session's feed, oldest first, optionally narrowed to one actor.

    Filtered HERE rather than by the route, because the route does not filter:
    one page is the whole session's feed and a frontend showing one actor's
    thread picks it out. So does this — which keeps a feed read at one actor
    evidence about attribution, since the thing being filtered is the entry's own
    `actor_id` rather than a query the server might get wrong in our favour.
    """
    page = daemon.read(
        f"/sessionData/{session_id}/entries?limit={ENTRY_LIMIT}", ENTRY_PAGE
    )
    if actor_id is None:
        return page.items
    return tuple(entry for entry in page.items if entry.actor_id == actor_id)


def messages(items: Entries) -> list[tuple[EntryResponse, MessageBodyResponse]]:
    """Every message entry, paired with its own body.

    The pair is what makes the rest of this file typed. An entry's `type` and its
    body class say the same thing, but only one of them is a thing a type checker
    can follow — so the narrowing happens here, once, and every reader below
    works on a body whose fields are known rather than on a union of twenty-four.
    """
    return [
        (entry, entry.body)
        for entry in items
        if isinstance(entry.body, MessageBodyResponse)
    ]


def prompts(items: Entries) -> list[EntryResponse]:
    """What a person sent. `phase == "prompt"` and not merely `role == "user"`:
    a harness injects user-role messages of its own (a compaction recap, a
    system reminder), and those are not something anybody typed."""
    return [
        entry for entry, body in messages(items)
        if body.role == "user" and body.phase == "prompt"
    ]


def assistant_messages(items: Entries) -> list[EntryResponse]:
    """The assistant's own bubbles, oldest first."""
    return [
        entry for entry, body in messages(items)
        if body.role == "assistant" and body.recipient_actor_id is None
    ]


def turn_enders(items: Entries) -> list[EntryResponse]:
    """The assistant bubbles that are where the model STOPPED.

    `phase == "end_turn"` is asserted per harness because each derives it from
    its own field — Claude Code from the response's `stop_reason`, Codex from the
    rollout item's `phase: "final_answer"` — and a release that renames either
    one breaks it in exactly one harness.
    """
    ending = {entry.entry_id for entry, body in messages(items) if body.phase == "end_turn"}
    return [entry for entry in assistant_messages(items) if entry.entry_id in ending]


def text(entry: EntryResponse) -> str:
    """A message's own prose. Embedded, so this is a field read and not a fetch —
    the ⧉copy link and the route behind it are both gone."""
    return entry.body.content.text if isinstance(entry.body, MessageBodyResponse) else ""


# --- commands, folded --------------------------------------------------------


@dataclass
class Shell:
    """One command, folded from its entries the way every client folds it.

    `mode == "replace"` is why the chunks cannot simply be concatenated — a
    harness that reports its whole output at once sends one replacing chunk, and
    appending it to what the file watch already streamed would double it. The
    `status` stream is kept apart because that is where a MONITOR's ticks
    arrive: a monitor's events and a command's stdout are two different claims
    and a scenario asserts on one of them at a time.
    """

    shell_id: str
    command: str
    execution: str
    output: str = ""
    status: str = ""
    state: str | None = None
    exit_code: int | None = None
    backgrounded: bool = False
    entry_ids: list[str] = field(default_factory=list)


SHELL_BODIES = (
    ShellStartedBodyResponse,
    ShellOutputBodyResponse,
    ShellBackgroundedBodyResponse,
    ShellFinishedBodyResponse,
)


def shells(items: Entries) -> list[Shell]:
    """Every command in the feed, oldest first, each one whole."""
    folded: dict[str, Shell] = {}
    for entry in items:
        body = entry.body
        if not isinstance(body, SHELL_BODIES):
            continue
        if isinstance(body, ShellStartedBodyResponse):
            folded[body.shell_id] = Shell(
                shell_id=body.shell_id,
                command=body.command.text,
                execution=body.execution,
            )
        found = folded.get(body.shell_id)
        if found is None:
            continue                     # a command whose start is not in this page
        found.entry_ids.append(entry.entry_id)
        if isinstance(body, ShellOutputBodyResponse):
            current = found.status if body.stream == "status" else found.output
            value = body.content.text if body.mode == "replace" else current + body.content.text
            if body.stream == "status":
                found.status = value
            else:
                found.output = value
        elif isinstance(body, ShellBackgroundedBodyResponse):
            found.backgrounded = True
        elif isinstance(body, ShellFinishedBodyResponse):
            found.state = body.state
            found.exit_code = body.exit_code
            # The whole output at once, from a harness that streams none of it.
            if body.result is not None and body.result.text:
                found.output = body.result.text
    return list(folded.values())


def shell(items: Entries, command: str) -> Shell | None:
    """The folded command whose text contains `command`, latest first: a
    scenario names the command it just asked for, and a workspace that ran the
    same thing in an earlier scenario must not answer for it."""
    for found in reversed(shells(items)):
        if command in found.command:
            return found
    return None


# --- delegations -------------------------------------------------------------


@dataclass
class Assignment:
    """One delegation: what the lead asked for, and what came back.

    Two entries, like a command, and for the same reason — the start is the tool
    call the lead made and the end is the AGENT's own report, arriving long
    after the call returned.
    """

    assignment_id: str
    assigned_actor_name: str | None
    state: str | None = None
    result: str = ""


def assignments(items: Entries) -> list[Assignment]:
    folded: dict[str, Assignment] = {}
    for entry in items:
        body = entry.body
        if isinstance(body, AssignmentStartedBodyResponse):
            folded[body.assignment_id] = Assignment(
                assignment_id=body.assignment_id,
                assigned_actor_name=body.assigned_actor_name,
            )
        elif isinstance(body, AssignmentFinishedBodyResponse):
            found = folded.setdefault(
                body.assignment_id,
                Assignment(assignment_id=body.assignment_id, assigned_actor_name=None),
            )
            found.state = body.state
            found.result = body.result.text if body.result is not None else ""
    return list(folded.values())


def model_matches(reported: str | None, requested: str) -> bool:
    """Whether the model the harness reported is the one that was asked for.

    Not equality: a launch selection is an alias or a family (`haiku`,
    `gpt-5.6-luna`) and a harness reports a resolved id
    (`claude-haiku-4-5-20251001`). One containing the other is the honest test —
    it still fails when a launch silently lands on a different model, which is
    the drift worth catching.
    """
    native = (reported or "").lower()
    wanted = requested.lower()
    return bool(native) and (wanted in native or native in wanted)


# --- the machinery's own verdicts, which have no route ----------------------


def unverdicted_count(daemon: Daemon) -> int:
    """Raw events the interpreter has recorded but not yet ruled on. Waiting for
    this to reach zero is what makes the verdict checks below race-free."""
    recorder = SqliteRawEventRepository(read_only(main_database(daemon.main_database_path)))
    return len(recorder.unverdicted(100_000))


def uninterpreted(daemon: Daemon, session_id: str) -> tuple[str, ...]:
    """Every raw event in the session the interpreter did not understand, as
    readable lines. Empty is the only acceptable answer: a session that produced
    one of these is a session whose harness said something new."""
    audits = SqliteRawEventAuditRepository(
        read_only(main_database(daemon.main_database_path))
    ).audits_for_session(SessionId(session_id))
    return tuple(
        f"{audit.raw_event.source_type}:{audit.raw_event.source_position} "
        f"{audit.interpretation.decision} ({audit.interpretation.reason or 'no reason given'}) "
        f"{audit.raw_event.payload[:300]!r}"
        for audit in audits
        if audit.interpretation is not None and audit.interpretation.decision in UNINTERPRETED
    )


def audit_errors(daemon: Daemon, session_id: str) -> tuple[str, ...]:
    """The session's rows behind the dashboard's ⚠ light."""
    errors = SqliteAuditReadRepository(
        read_only(audit_database(daemon.audit_database_path))
    ).errors_for_session(SessionId(session_id))
    return tuple(f"{error.component} · {error.action}: {error.context}" for error in errors)
