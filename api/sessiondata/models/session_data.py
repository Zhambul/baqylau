# One session's aggregate, as both frontends receive it.
#
# Everything actor-specific sits on the actor, because that is where the
# harnesses report it: a session with a lead and three subagents has four
# models, four statuses and four scoreboards, and one of each on the session
# would have to pick a winner.
from typing import Literal, TypeAlias

from pydantic import BaseModel

from api.common.models.values.account_reference import AccountReferenceResponse
from api.common.models.values.repository_status import RepositoryStatusResponse
from api.common.models.values.token_usage import TokenUsageResponse

ActorStatusResponse: TypeAlias = Literal[
    "idle",
    "thinking",
    "working",
    "executing",
    "awaiting_background",
    "awaiting_attention",
    "awaiting_response",
]


class GoalResponse(BaseModel):
    objective: str | None
    completed: bool


class TaskResponse(BaseModel):
    task_id: str
    subject: str
    description: str | None
    state: Literal["pending", "in_progress", "completed", "deleted"]
    owner_actor_id: str | None


class SessionResponse(BaseModel):
    """The session's own FACTS — everything about it that a stored event said.

    Nothing read-time is in here, and that is what lets an SSE frame carry this
    same shape: a frame is what the read model committed, and a client that
    applied one would otherwise clobber the world's state (see
    `SessionDataResponse.live`) with an absent field.
    """

    session_id: str
    harness: str
    title: str | None
    state: Literal["running", "finished"]
    working_directory: str
    started_at: float | None
    finished_at: float | None
    account: AccountReferenceResponse | None
    lead_actor_id: str
    goal: GoalResponse | None
    tasks: tuple[TaskResponse, ...]


class ActorUsageResponse(BaseModel):
    tokens: TokenUsageResponse
    cost_in_usd: str | None


class ActorContextResponse(BaseModel):
    used_tokens: int
    window_tokens: int
    compacting: bool


class ActorBackgroundResponse(BaseModel):
    """What is still running, and how much of it there has been. The ids are the
    running ones; the counts are every one this actor ever started."""

    running_shell_ids: tuple[str, ...]
    monitor_count: int
    background_job_count: int


class ToolCountResponse(BaseModel):
    tool: str
    count: int


class ActorStatisticsResponse(BaseModel):
    """The scoreboard. `active_seconds` includes the interval still open, which
    the route measures against now — the stored number is the closed ones.

    `active` is what makes that number usable on a surface that repaints between
    frames: a client can only carry the clock forward if it knows the interval is
    STILL open, and frames arrive on change rather than on a tick. It is data, not
    presentation — whether this actor is inside a working interval right now —
    which is why it rides here instead of being inferred from `status`.
    """

    prompt_count: int
    shell_command_count: int
    failed_shell_command_count: int
    file_count: int
    lines_added: int
    lines_removed: int
    actor_message_count: int
    tool_counts: tuple[ToolCountResponse, ...]
    active_seconds: float
    active: bool


class ActorResponse(BaseModel):
    # Redundant inside a SessionDataResponse, essential inside a
    # GlobalStreamFrame: a frame carries bare actor rows, and the client can
    # only merge one into its list by knowing which session it belongs to.
    # Without it every actor-bearing frame degraded into a full list re-read.
    session_id: str
    actor_id: str
    parent_actor_id: str | None
    role: Literal["lead", "child", "teammate", "sidecar"]
    name: str
    description: str | None
    state: Literal["running", "finished"]
    started_at: float | None
    finished_at: float | None
    model: str | None
    effort: str | None
    status: ActorStatusResponse | None
    usage: ActorUsageResponse
    context: ActorContextResponse
    background: ActorBackgroundResponse
    statistics: ActorStatisticsResponse


class SessionDataResponse(BaseModel):
    """The snapshot a client starts from.

    `cursor` is the session's high-water mark across its entries AND its
    aggregate revisions, read in the same transaction as the rows — so the
    entries page taken `at` it and the stream opened from it describe one
    instant. The aggregate's own revision would not do: it routinely lags the
    newest entry, and starting there re-sends what the client already has.
    """

    cursor: int
    session: SessionResponse
    actors: tuple[ActorResponse, ...]
    # The two READ-TIME truths, beside the facts rather than inside them:
    # whether a terminal window is attached right now, and what git says about
    # the working directory right now. Neither is event-sourced, neither is
    # stored, and neither can ride a stream frame — so they belong to the
    # ANSWER, not to the session.
    live: bool
    repository: RepositoryStatusResponse | None


class SessionDataListResponse(BaseModel):
    """The list view: every visible session, and the cursor to open the global
    stream from.

    `cursor` is the read model's high-water mark AT THE SAME READ as `sessions`
    — so a stream opened from it carries only what committed after this list,
    and never the backlog a stream opened from 0 would replay as if every
    session had just been born.
    """

    cursor: int
    sessions: tuple[SessionDataResponse, ...]
