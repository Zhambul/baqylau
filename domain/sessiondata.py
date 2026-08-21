"""SessionData: everything a frontend shows about a session, as stored facts.

The read model, and the whole of it besides the feed (`domain/entries.py`).
Written at push time by the writers in `engine/sessiondata/`, read back in one
indexed query. Nothing here is folded when someone asks; the fold happened once,
when the fact arrived.

Two fields the aggregate deliberately does NOT carry are computed by the route
that answers — whether a terminal window is attached, and what git says about
the working directory. They are read-time truths about the world, not facts
about the session, and storing them would mean writing a row when nothing
happened.

The `*_internal` fields are the writers' own memory, kept here because a restart
has to resume the fold exactly where it stopped and the alternative is
re-reading the whole canonical log. They are never served.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal, TypeAlias

from domain.ids import ActorId, AttentionId, HarnessName, SessionId, ShellId, TaskId
from domain.stored import STORED
from domain.values import AccountReference, ActorRole, ModelReference, TokenUsage

# What an actor is doing, in the one word a tab colour and a list row need.
# Ordered as the fold reaches them, not by severity: `idle` is a session that
# has started and nothing more, `awaiting_response` a turn that ended.
ActorStatus: TypeAlias = Literal[
    "idle",
    "thinking",
    "working",
    "executing",
    "awaiting_background",
    "awaiting_attention",
    "awaiting_response",
]
LifecycleState: TypeAlias = Literal["running", "finished"]
TaskState: TypeAlias = Literal["pending", "in_progress", "completed", "deleted"]


@dataclass(frozen=True)
class SessionGoal:
    __pydantic_config__ = STORED

    objective: str | None
    completed: bool


@dataclass(frozen=True)
class SessionTask:
    __pydantic_config__ = STORED

    task_id: TaskId
    subject: str
    description: str | None
    state: TaskState
    owner_actor_id: ActorId | None


@dataclass(frozen=True)
class SessionFacts:
    """The session half of the aggregate — one row of `session_data`."""

    __pydantic_config__ = STORED

    session_id: SessionId
    harness: HarnessName
    state: LifecycleState
    working_directory: str
    started_at: float | None
    lead_actor_id: ActorId
    title: str | None = None
    finished_at: float | None = None
    account: AccountReference | None = None
    goal: SessionGoal | None = None
    tasks: tuple[SessionTask, ...] = ()
    # A title derived from the first prompt: the only title a session has
    # until a harness names it, and some never do. Kept apart from `title`
    # because a real title must always win however late it arrives, and the
    # fold sees the four sources in any order.
    prompt_title_internal: str | None = None
    custom_title_internal: str | None = None
    automatic_title_internal: str | None = None
    summary_title_internal: str | None = None
    # The membership `task.list_changed` declares, which orders `tasks` and
    # decides what belongs to the list at all.
    task_order_internal: tuple[TaskId, ...] = ()


@dataclass(frozen=True)
class ActorUsage:
    __pydantic_config__ = STORED

    tokens: TokenUsage = field(default_factory=TokenUsage)
    cost_in_usd: Decimal | None = None


@dataclass(frozen=True)
class ActorContext:
    __pydantic_config__ = STORED

    used_tokens: int = 0
    window_tokens: int = 0
    compacting: bool = False


@dataclass(frozen=True)
class ActorBackground:
    __pydantic_config__ = STORED

    running_shell_ids: tuple[ShellId, ...] = ()
    monitor_count: int = 0
    background_job_count: int = 0


@dataclass(frozen=True)
class ActorStatistics:
    __pydantic_config__ = STORED

    prompt_count: int = 0
    shell_command_count: int = 0
    failed_shell_command_count: int = 0
    file_count: int = 0
    lines_added: int = 0
    lines_removed: int = 0
    actor_message_count: int = 0
    tool_counts: tuple[tuple[str, int], ...] = ()
    # Closed intervals only — prompt to turn end. The interval still open when
    # the aggregate is read is added by the route that answers, because its
    # length is the current time and no fact says what that is.
    active_seconds: float = 0.0
    active_since_internal: float | None = None
    # `file_count` counts DISTINCT paths, so the paths themselves have to be
    # remembered; a count alone cannot tell a second edit of one file from a
    # first edit of two.
    file_paths_internal: tuple[str, ...] = ()


@dataclass(frozen=True)
class ActorFacts:
    """One actor of the aggregate — one row of `session_data_actors`."""

    __pydantic_config__ = STORED

    session_id: SessionId
    actor_id: ActorId
    role: ActorRole
    name: str
    state: LifecycleState
    parent_actor_id: ActorId | None = None
    description: str | None = None
    started_at: float | None = None
    finished_at: float | None = None
    # The whole reference, not just the name a reader sees: relaunching a session
    # on the same model needs the harness's own id for it, and the display name
    # is not one. What the HTTP boundary carries is the name (api/sessiondata/mapper.py).
    model: ModelReference | None = None
    effort: str | None = None
    status: ActorStatus | None = None
    usage: ActorUsage = field(default_factory=ActorUsage)
    context: ActorContext = field(default_factory=ActorContext)
    background: ActorBackground = field(default_factory=ActorBackground)
    statistics: ActorStatistics = field(default_factory=ActorStatistics)
    # The status fold's own memory (Table 0): which attentions are still
    # pending, so a restart resumes on the same branch rather than one where
    # nothing was ever asked.
    pending_attention_internal: tuple[AttentionId, ...] = ()


@dataclass(frozen=True)
class SessionData:
    """One session's whole aggregate, read in one transaction.

    `cursor` is the session's high-water mark across its entries AND its
    aggregate revisions — the boundary a stream starts from. The aggregate's own
    revision is NOT that boundary: it routinely lags the newest entry, and
    starting there would send the client entries it already has.
    """

    session: SessionFacts
    actors: tuple[ActorFacts, ...]
    cursor: int
    # When this session last did anything, read from its newest entry. Read-time
    # like the cursor, and for the same reason: storing it on the session row
    # would rewrite that row on every single fact, so the one field nobody
    # streams would be the one making every session's row change constantly.
    last_activity_at: float | None = None
