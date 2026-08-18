"""What the dashboard says, read back the way the browser reads it.

Every assertion in this suite goes through `/api/sessions…` — the same routes and
the same rendered items the page draws, so an item asserted here is an item the
browser would show. A later Playwright tier asserts the DOM those items become;
it does not need a second way to find them.

Read back into the ROUTES' OWN RESPONSE MODELS, never into dicts. Every reader
below returns a real type, so `item.state` is checked at the point it is written
and a renamed field breaks the rig at the read rather than at a timeout twenty
lines later — which is what `item.get("state")` on an untyped dict bought us:
None, no error, and a scenario waiting out its clock for a state nobody was
going to send. The adapters are built once, here, because they ARE the contract
this suite reads.

The two verdict readers at the bottom have no route because nothing in the
product asks for them over HTTP: they read `main.db` / `audit.db` through the
repositories that own those tables, which is also how `bin/baqylau-raw-events-audit.py`
reads them.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from typing import TypeAlias, TypeVar
from urllib.parse import quote

from pydantic import TypeAdapter

from api.dashboard.models.sessions.activity_item import ActivityItemResponse
from api.dashboard.models.sessions.activity_page import ActivityPageResponse
from api.dashboard.models.sessions.actor_summary import ActorSummaryResponse
from api.dashboard.models.sessions.activity_statistics import ActivityStatisticsResponse
from api.dashboard.models.sessions.background_work import BackgroundOperationResponse
from api.dashboard.models.sessions.session_list_item import SessionListItemResponse
from api.dashboard.models.sessions.session_snapshot_response import SessionSnapshotResponse
from api.dashboard.models.sessions.session_summary import SessionSummaryResponse
from api.common.models.values.model_reference import ModelReferenceResponse
from domain.ids import SessionId
from repository.impl.sqlite.audit import SqliteAuditReadRepository
from repository.impl.sqlite.databases import audit_database, main_database, read_only
from repository.impl.sqlite.raw_event_audits import SqliteRawEventAuditRepository
from repository.impl.sqlite.raw_events import SqliteRawEventRepository
from support.daemon import Daemon

T = TypeVar("T")

POLL_SECONDS = 0.5

# One adapter per resource this suite reads: the model the route declares.
SESSION_LIST = TypeAdapter(tuple[SessionListItemResponse, ...])
SESSION_SNAPSHOT = TypeAdapter(SessionSnapshotResponse)
ACTIVITY_PAGE = TypeAdapter(ActivityPageResponse)

# The verdicts that mean the interpreter did NOT understand what a harness said.
# `ignored_nonsemantic` is not one of them: that is the interpreter recognising a
# record and having nothing to say about it.
UNINTERPRETED = ("ignored_unknown", "translation_failed")


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


def session_ids(daemon: Daemon) -> frozenset[str]:
    """Every session the daemon knows about — the "before" of a launch."""
    return frozenset(
        row.session.session_id for row in daemon.read("/api/sessions", SESSION_LIST)
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
    to end in a session that had already finished). A launch response cannot
    settle it either: the harness, not the launcher, chooses the session id and
    announces it only in its own first evidence.
    """
    appeared = [
        row.session for row in daemon.read("/api/sessions", SESSION_LIST)
        if row.session.session_id not in known
        and row.session.harness == harness
        and row.session.initial_working_directory == workspace
    ]
    if not appeared:
        return None
    return max(appeared, key=lambda session: session.started_at).session_id


def snapshot(daemon: Daemon, session_id: str) -> SessionSnapshotResponse:
    """The session page's whole reply — both halves, as the browser gets it."""
    return daemon.read(f"/api/sessions/{session_id}", SESSION_SNAPSHOT)


def session(daemon: Daemon, session_id: str) -> SessionSummaryResponse | None:
    """What the session IS. None until its first fact has been interpreted —
    the row is born by the reaction to `session.started`, not by the launch."""
    return snapshot(daemon, session_id).canonical.session


def tab_state(daemon: Daemon, session_id: str) -> str | None:
    """The session's own liveness verdict — the projection behind the tab colour
    and the red/green alerts (`engine/projections/tabstate.py`). Asked instead of
    scanning for a TurnFinished event so that "the turn ended" means here exactly
    what it means to the product."""
    return snapshot(daemon, session_id).canonical.tab_state


def unverdicted_count(daemon: Daemon) -> int:
    """Raw events the interpreter has recorded but not yet ruled on. Waiting for
    this to reach zero is what makes the verdict checks below race-free."""
    recorder = SqliteRawEventRepository(read_only(main_database(daemon.main_database_path)))
    return len(recorder.unverdicted(100_000))


def feed(
    daemon: Daemon, session_id: str, actor_id: str | None = None
) -> tuple[ActivityItemResponse, ...]:
    """The activity of ONE actor in the session, oldest first — the browser's
    backlog request, and with `actor_id` the request it makes when the reader
    switches to a subagent's thread.

    An actor is always named, even when it is the lead: the route defaults the
    scope to the lead itself (api/dashboard/sessions.py `_scope`), so "the
    session's feed" is the lead's feed. A subagent's work is not in it — which is
    what makes a feed read at one actor evidence about attribution rather than
    just about arrival.
    """
    query = f"?block_count=200&actor_id={quote(actor_id)}" if actor_id else "?block_count=200"
    return daemon.read(f"/api/sessions/{session_id}/activity{query}", ACTIVITY_PAGE).items


def actors(daemon: Daemon, session_id: str) -> tuple[ActorSummaryResponse, ...]:
    """Who is working in this session — the lead and every subagent under it, as
    the actor switcher lists them (`actors` on the session snapshot)."""
    return snapshot(daemon, session_id).canonical.actors


def subagents(daemon: Daemon, session_id: str) -> list[ActorSummaryResponse]:
    """Every actor that is not the lead. Identified by HAVING A PARENT rather
    than by its role: `child` and `teammate` are two kinds of subagent and a
    harness may add a third, while an actor nobody launched is the lead."""
    return [actor for actor in actors(daemon, session_id) if actor.parent_actor_id]


Items: TypeAlias = Sequence[ActivityItemResponse]


def assignments(items: Items) -> list[ActivityItemResponse]:
    """The delegations in a feed — work this actor handed to another one."""
    return [item for item in items if item.item_type == "actor_assignment"]


def prompts(items: Items) -> list[ActivityItemResponse]:
    return [item for item in items if item.conversation_kind == "prompt"]


def assistant_messages(items: Items) -> list[ActivityItemResponse]:
    """The assistant's own bubbles, oldest first."""
    return [
        item for item in items
        if item.item_type == "message" and item.conversation_kind == "message"
    ]


def statistics(daemon: Daemon, session_id: str) -> ActivityStatisticsResponse:
    """The counters the scorebar draws (commands, files, lines)."""
    return snapshot(daemon, session_id).canonical.statistics


def background_jobs(
    daemon: Daemon, session_id: str
) -> tuple[BackgroundOperationResponse, ...]:
    """The session's background jobs, as the jobs tab lists them.

    Read from the session snapshot rather than the feed: a job is tracked APART
    from the turn that started it (`background_work` on the snapshot), which is
    the whole point of backgrounding one.
    """
    return snapshot(daemon, session_id).canonical.background_work.jobs


def monitors(daemon: Daemon, session_id: str) -> tuple[BackgroundOperationResponse, ...]:
    """The session's monitors, as the monitors tab lists them.

    The same shape as a background job and a different fact: a job is a command
    whose output outlives its turn, a monitor is a watch ARMED to report events
    until it is stopped. The dashboard keeps them in two lists off the same
    snapshot, so a monitor filed as a job (or the reverse) is a visible failure.
    """
    return snapshot(daemon, session_id).canonical.background_work.monitors


def shell_operations(items: Items) -> list[ActivityItemResponse]:
    return [item for item in items if item.summary_kind == "shell"]


def content(daemon: Daemon, reference: str) -> str:
    """One content reference resolved — the request the feed's ⧉copy link makes."""
    return daemon.get_text("/api/content/" + quote(reference, safe=""))


def item_text(daemon: Daemon, item: ActivityItemResponse) -> str:
    """An item's own text, whichever side of the ⧉ link the feed put it on. The
    same two-step as operation_output, for the items whose payload is prose."""
    if item.content_reference:
        return content(daemon, item.content_reference)
    return item.plain_text


def operation_command(daemon: Daemon, item: ActivityItemResponse) -> str:
    """What an operation RAN. Behind a content reference rather than in the item:
    the feed shows a command's first line and fetches the rest on demand, so this
    is the same two-step the browser does."""
    return content(daemon, item.command_reference) if item.command_reference else ""


def operation_output(daemon: Daemon, item: ActivityItemResponse) -> str:
    """What an operation PRINTED. `plain_text` already carries it for anything
    short (dashboard/render/items/operations.py operation_text); the reference is
    the path for output too large to inline."""
    if item.output_reference:
        return content(daemon, item.output_reference)
    return item.plain_text


def turn_enders(items: Items) -> list[ActivityItemResponse]:
    """The assistant bubbles the feed marks as where the model stopped.

    `item["final"]` is the presenter's name for the canonical `phase ==
    "end_turn"` (dashboard/render/items/messages.py). Asserted per harness because
    each derives it from its own field — Claude Code from the response's
    `stop_reason`, Codex from the rollout item's `phase: "final_answer"` — and a
    release that renames either one breaks it in exactly one harness.
    """
    return [item for item in assistant_messages(items) if item.final]


def model_matches(reported: ModelReferenceResponse | None, requested: str) -> bool:
    """Whether the model the harness reported is the one that was asked for.

    Not equality: a launch selection is an alias or a family (`haiku`,
    `gpt-5.6-luna`) and a harness reports a resolved id
    (`claude-haiku-4-5-20251001`). One containing the other is the honest test —
    it still fails when a launch silently lands on a different model, which is
    the drift worth catching.
    """
    native = (reported.native_id if reported else "").lower()
    wanted = requested.lower()
    return bool(native) and (wanted in native or native in wanted)


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
