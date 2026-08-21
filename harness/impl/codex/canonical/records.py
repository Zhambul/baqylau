# harness/impl/codex/canonical/records.py — the codex rollout's DECLARED shapes.
#
# Two different kinds of shape live here, and they are validated differently:
#
#   FOREIGN payloads (suffix `Payload`, `Item`, `Arguments`) are codex's OWN
#   JSON, read with `pydantic.BaseModel` and `extra="forbid"` (the FOREIGN
#   config below). A payload that does not match — a missing field, a wrong
#   type, or an EXTRA field codex did not used to send — raises
#   `pydantic.ValidationError`, which the interpreter loop (engine/interpret/
#   loop.py) already turns into the `translation_failed` verdict naming the
#   error. This is the owner's decision (TASKS.md, 2026-08-21): a foreign
#   record either matches exactly what we declared, or translation stops
#   until the new field is declared. Rebuild heals history afterwards.
#
#   Each field below is transcribed from what rollout.py / events.py /
#   items.py actually READ before this module existed — the dict-walking WAS
#   the de-facto schema (docs/styleguide.md single-owner table: this module
#   is the one owner of it now). A field genuinely never read anywhere below
#   this file is not declared, on purpose: declaring a guessed field would be
#   inventing a fact about codex's JSON format nobody has verified.
#
#   ROLLOUT records (no suffix, one per `KINDS` entry in rollout.py) are OUR
#   OWN typed carrier of what a payload PARSED to. Nothing foreign reaches a
#   translator through them, so they are ordinary frozen dataclasses — the
#   same shape domain/events.py's CanonicalEvent payloads take — not pydantic
#   models. Each carries a `kind: Literal[...]` tag so a reader (translator.py)
#   narrows on it exactly as it narrowed on the `record["kind"]` string before.
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias, Union

from pydantic import BaseModel, ConfigDict, JsonValue

from domain.ids import ActorId, CallId, HarnessSessionId, ShellNativeId, TurnId

# The one config every FOREIGN payload model shares: an unknown field is
# schema drift, not tolerance (the STRICTEST stance the owner chose — see the
# module header). `frozen=True` because a payload is read, never edited.
FOREIGN = ConfigDict(extra="forbid", frozen=True)

# The escape hatch for the one place reality is open by the VENDOR's own
# contract, not by an oversight of ours: a Codex multi-agent tool call
# (`spawn_agent` and friends) carries an argument object shaped by the verb
# codex is running, most of which our translator never reads (only
# `send_message`'s `message`/`content` do — see SendMessageArguments below).
# Declaring an empty `extra="forbid"` model for the other five verbs would
# fail on the first real call, for fields that mean nothing to us; this
# config says so explicitly rather than leaving the field `Any`.
OPEN_FOREIGN = ConfigDict(extra="ignore", frozen=True)


# === FOREIGN: the event_msg register's `payload` (events.py) ================


class TokenUsageBlock(BaseModel):
    """One `total_token_usage` / `last_token_usage` snapshot."""

    model_config = FOREIGN
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    total_tokens: int | None = None


class TokenCountInfo(BaseModel):
    model_config = FOREIGN
    total_token_usage: TokenUsageBlock | None = None
    last_token_usage: TokenUsageBlock | None = None
    model_context_window: int | None = None


class RateLimitWindow(BaseModel):
    model_config = FOREIGN
    used_percent: float | None = None
    window_minutes: int | None = None
    resets_at: int | None = None


class RateLimitsBlock(BaseModel):
    model_config = FOREIGN
    plan_type: str | None = None
    primary: RateLimitWindow | None = None
    secondary: RateLimitWindow | None = None


class TokenCountPayload(BaseModel):
    """A `token_count` event_msg payload. `info` is null on a rate-limit-only
    event (events.py _ev_token_count); `rate_limits` rides the same event on
    an independent nullable field (events.py rate_limits())."""

    model_config = FOREIGN
    type: Literal["token_count"] = "token_count"
    info: TokenCountInfo | None = None
    rate_limits: RateLimitsBlock | None = None


class GoalBlock(BaseModel):
    model_config = FOREIGN
    objective: str | None = None
    status: str | None = None
    reason: str | None = None


class ThreadGoalUpdatedPayload(BaseModel):
    model_config = FOREIGN
    type: Literal["thread_goal_updated"] = "thread_goal_updated"
    goal: GoalBlock | None = None


class EmptyPayload(BaseModel):
    """A payload whose handler reads nothing from it: `thread_goal_cleared`,
    `context_compacted`. Declared (rather than skipped) so an unexpected
    field on one of these still fails fast instead of silently riding along
    unread. Shared by both `type` strings, so `type` itself is read but not
    constrained to one of them here — the dispatch table that chose this
    model already did that check."""

    model_config = FOREIGN
    type: str | None = None


class WorldStatePayload(BaseModel):
    """A `world_state` top-level record: a large periodic state snapshot
    (open files, shell sessions, todos) — GENUINELY open (module header,
    OPEN_FOREIGN), not a shape this codebase has ever read one field of, let
    alone declared exhaustively."""

    model_config = OPEN_FOREIGN


class TaskStartedPayload(BaseModel):
    model_config = FOREIGN
    type: Literal["task_started"] = "task_started"
    started_at: str | int | float | None = None
    turn_id: TurnId | None = None


class TaskCompletePayload(BaseModel):
    model_config = FOREIGN
    type: Literal["task_complete"] = "task_complete"
    completed_at: str | int | float | None = None
    turn_id: TurnId | None = None
    last_agent_message: str | None = None


class ThreadSettingsBlock(BaseModel):
    model_config = FOREIGN
    model: str | None = None
    reasoning_effort: str | None = None


class ThreadSettingsAppliedPayload(BaseModel):
    model_config = FOREIGN
    type: Literal["thread_settings_applied"] = "thread_settings_applied"
    thread_settings: ThreadSettingsBlock | None = None


class FileChangeEntry(BaseModel):
    model_config = FOREIGN
    type: str | None = None
    content: str | None = None
    unified_diff: str | None = None
    move_path: str | None = None


class FileChangeItem(BaseModel):
    model_config = FOREIGN
    type: Literal["FileChange"]
    id: str | None = None
    status: str | None = None
    changes: dict[str, FileChangeEntry] | None = None


class CommandExecutionItem(BaseModel):
    model_config = FOREIGN
    type: Literal["CommandExecution"]
    id: str | None = None
    status: str | None = None
    process_id: ShellNativeId | int | None = None
    aggregated_output: str | None = None
    formatted_output: str | None = None
    stdout: str | None = None
    stderr: str | None = None
    exit_code: int | None = None


class SubAgentActivityItem(BaseModel):
    model_config = FOREIGN
    type: Literal["SubAgentActivity"]
    kind: str | None = None
    agent_thread_id: ActorId | None = None
    agent_path: str | None = None
    id: str | None = None


class PlanItem(BaseModel):
    model_config = FOREIGN
    type: Literal["Plan"]
    text: str | None = None
    id: str | None = None


class CoveredItem(BaseModel):
    """`UserMessage` / `AgentMessage` / `Reasoning` — content this register
    ALSO carries the record for, read there instead (events.COVERED_ITEMS).

    Open on purpose (OPEN_FOREIGN, module header): the whole point of this
    model is that NOTHING on it is read, only its `type`, so its other
    fields (the very content the other register already delivers) are not
    worth declaring precisely — the shape lives in the response_item models
    that actually read it (items.MessagePayload, items.ReasoningPayload)."""

    model_config = OPEN_FOREIGN
    type: Literal["UserMessage", "AgentMessage", "Reasoning"]


ItemCompletedItem: TypeAlias = Union[
    FileChangeItem, CommandExecutionItem, SubAgentActivityItem, PlanItem, CoveredItem,
]

# `item.type` -> the declared model for it. A plain dict, not a pydantic
# discriminated union: pydantic's "smart" union mode picks by a coercion-cost
# heuristic, not by the discriminator alone, and a permissive catch-all member
# in the union (needed for an UNKNOWN item type to fall through, not fail)
# skewed that heuristic — measured picking the catch-all over an exact
# `Literal["FileChange"]` match. Dispatching on this dict FIRST, exactly like
# rollout.EVENTS/RESPONSES, keeps "unknown type" and "known type, bad shape"
# the two separate outcomes the owner's decision needs them to be.
ITEM_COMPLETED_ITEMS: dict[str, type[ItemCompletedItem]] = {
    "FileChange": FileChangeItem,
    "CommandExecution": CommandExecutionItem,
    "SubAgentActivity": SubAgentActivityItem,
    "Plan": PlanItem,
    "UserMessage": CoveredItem,
    "AgentMessage": CoveredItem,
    "Reasoning": CoveredItem,
}


class ItemCompletedPayload(BaseModel):
    model_config = FOREIGN
    type: Literal["item_completed"] = "item_completed"
    turn_id: TurnId | None = None
    started_at_ms: int | None = None
    item: dict[str, JsonValue] | None = None


class TurnAbortedPayload(BaseModel):
    model_config = FOREIGN
    type: Literal["turn_aborted"] = "turn_aborted"
    turn_id: TurnId | None = None
    reason: str | None = None


class UserMessagePayload(BaseModel):
    model_config = FOREIGN
    type: Literal["user_message"] = "user_message"
    message: str | None = None


class AgentReasoningPayload(BaseModel):
    model_config = FOREIGN
    type: Literal["agent_reasoning"] = "agent_reasoning"
    text: str | None = None


class AgentMessagePayload(BaseModel):
    model_config = FOREIGN
    type: Literal["agent_message"] = "agent_message"
    message: str | None = None
    phase: str | None = None


class WebSearchAction(BaseModel):
    model_config = FOREIGN
    query: str | None = None


class WebSearchEndPayload(BaseModel):
    model_config = FOREIGN
    type: Literal["web_search_end"] = "web_search_end"
    query: str | None = None
    action: WebSearchAction | None = None


# === FOREIGN: the top-level register (rollout.py) ============================


class CollaborationModeSettings(BaseModel):
    model_config = FOREIGN
    reasoning_effort: str | None = None


class CollaborationMode(BaseModel):
    model_config = FOREIGN
    settings: CollaborationModeSettings | None = None


class TurnContextPayload(BaseModel):
    model_config = FOREIGN
    model: str | None = None
    effort: str | None = None
    collaboration_mode: CollaborationMode | None = None


class CompactedPayload(BaseModel):
    model_config = FOREIGN
    message: str | None = None
    # The entire rewritten conversation — deliberately never modeled beyond
    # its length (rollout._top_compacted): a record shape must not be a
    # megabyte, so this is read only as `len(...)`, never indexed into.
    replacement_history: list[JsonValue] | None = None
    window_id: str | int | None = None
    previous_window_id: str | int | None = None


class ThreadSpawn(BaseModel):
    model_config = FOREIGN
    parent_thread_id: HarnessSessionId | None = None
    agent_path: str | None = None


class SubagentSource(BaseModel):
    model_config = FOREIGN
    thread_spawn: ThreadSpawn | None = None


class SessionMetaSource(BaseModel):
    model_config = FOREIGN
    subagent: SubagentSource | None = None


class SessionMetaPayload(BaseModel):
    """A `session_meta` record's `payload` — read by sources.py (rollout
    ownership / parent-thread discovery) and translator.py (actor naming)."""

    model_config = FOREIGN
    id: str | None = None
    cwd: str | None = None
    timestamp: str | None = None
    thread_source: str | None = None
    parent_thread_id: HarnessSessionId | None = None
    # A subagent's spawn detail (SessionMetaSource) OR a plain string naming
    # WHAT started the session ("vscode", the IDE extension) — codex uses the
    # one field for both.
    source: SessionMetaSource | str | None = None
    originator: str | None = None


class CodexHookPayload(BaseModel):
    """A codex hook delivery's JSON body — GENUINELY open (module header,
    OPEN_FOREIGN): unlike a rollout record, a hook delivery's field set varies
    by `hook_event_name` (SessionStart/PreCompact/PostCompact/…), most of
    which this translator never reads and has no fixture corpus to declare
    exhaustively. Declared as far as reality allows: the seven fields
    translator._translate_hook actually reads."""

    model_config = OPEN_FOREIGN
    hook_event_name: str | None = None
    hook_event_id: str | None = None
    uuid: str | None = None
    transcript_path: str | None = None
    cwd: str | None = None
    before_tokens: int | None = None
    after_tokens: int | None = None


# === FOREIGN: the response_item register (items.py) ==========================


class WebSearchCallAction(BaseModel):
    model_config = FOREIGN
    query: str | None = None


class WebSearchCallPayload(BaseModel):
    model_config = FOREIGN
    type: Literal["web_search_call"] = "web_search_call"
    id: str | None = None
    action: WebSearchCallAction | None = None


class ContentPart(BaseModel):
    model_config = FOREIGN
    type: str | None = None
    text: str | None = None


class FunctionCallOutputPayload(BaseModel):
    model_config = FOREIGN
    type: Literal["function_call_output"] = "function_call_output"
    id: str | None = None
    output: str | list[ContentPart | str] | None = None
    call_id: CallId | None = None


class ChatMessageMetadata(BaseModel):
    model_config = FOREIGN
    turn_id: TurnId | None = None


class MessagePayload(BaseModel):
    model_config = FOREIGN
    type: Literal["message"] = "message"
    id: str | None = None
    content: str | list[ContentPart | str] | None = None
    role: str | None = None
    phase: str | None = None
    internal_chat_message_metadata_passthrough: ChatMessageMetadata | None = None


class ReasoningPayload(BaseModel):
    model_config = FOREIGN
    type: Literal["reasoning"] = "reasoning"
    id: str | None = None
    summary: str | list[ContentPart | str] | None = None


class CustomToolCallPayload(BaseModel):
    model_config = FOREIGN
    type: Literal["custom_tool_call"] = "custom_tool_call"
    id: str | None = None
    name: str | None = None
    input: str | list[ContentPart | str] | None = None
    call_id: CallId | None = None


class CustomToolCallOutputPayload(BaseModel):
    model_config = FOREIGN
    type: Literal["custom_tool_call_output"] = "custom_tool_call_output"
    id: str | None = None
    output: str | list[ContentPart | str] | None = None
    call_id: CallId | None = None


class FunctionCallPayload(BaseModel):
    model_config = FOREIGN
    type: Literal["function_call"] = "function_call"
    id: str | None = None
    name: str | None = None
    call_id: CallId | None = None
    arguments: str | None = None


class CombinedCommandResult(BaseModel):
    model_config = OPEN_FOREIGN
    output: str | None = None
    exit_code: int | None = None
    session_id: ShellNativeId | int | None = None


class CombinedToolResult(BaseModel):
    """The `custom_tool_call_output` wrapper's own JSON body — GENUINELY open
    (module header, OPEN_FOREIGN): it freely combines an apply_patch result
    with a command result depending on what the model called in one exec turn,
    and the exact key set is the vendor wrapper's format, not a shape this
    codebase controls. Declared as far as reality allows: the keys our own
    logic reads (`patch`, `test`, `output`, `session_id`, `exit_code`); an
    unrecognised OTHER key rides along unread rather than failing the record,
    which a strict `extra="forbid"` sibling here would do."""

    model_config = OPEN_FOREIGN
    patch: str | None = None
    test: CombinedCommandResult | None = None
    output: str | None = None
    session_id: ShellNativeId | int | None = None
    exit_code: int | None = None


# --- function_call NAME -> its argument grammar ------------------------------
# `arguments` is a JSON *string*; these models are what it decodes to. A
# codex build that stops sending valid JSON there still degrades (items._args
# falls back to `{}`), so THIS gate never fires on that failure mode — only
# on a decoded object that carries a field none of these name.

class ExecArguments(BaseModel):
    model_config = FOREIGN
    cmd: str | list[str] | None = None
    command: str | list[str] | None = None


class StdinArguments(BaseModel):
    model_config = FOREIGN
    session_id: ShellNativeId | int | None = None
    chars: str | None = None


class AskOption(BaseModel):
    model_config = FOREIGN
    label: str | None = None
    description: str | None = None


class AskQuestion(BaseModel):
    model_config = FOREIGN
    id: str | None = None
    header: str | None = None
    question: str | None = None
    options: list[AskOption] | None = None


class AskArguments(BaseModel):
    model_config = FOREIGN
    questions: list[AskQuestion] | None = None


class PlanTask(BaseModel):
    model_config = FOREIGN
    step: str | None = None
    status: str | None = None


class PlanArguments(BaseModel):
    model_config = FOREIGN
    plan: list[PlanTask] | None = None


# The multi-agent verbs (collaboration.spawn_agent and friends): a GENUINELY
# open shape by the vendor's own contract (module header, OPEN_FOREIGN). Only
# `send_message`'s text is ever read (translator._translate_record, kind
# "actor_activity"), so only it is declared strict; the rest keep whatever
# other fields codex sends without this gate rejecting them for it.
class SendMessageArguments(BaseModel):
    model_config = FOREIGN
    message: str | None = None
    content: str | None = None
    target: str | None = None


class SpawnAgentArguments(BaseModel):
    model_config = OPEN_FOREIGN


class WaitAgentArguments(BaseModel):
    model_config = OPEN_FOREIGN


class InterruptAgentArguments(BaseModel):
    model_config = OPEN_FOREIGN


class ListAgentsArguments(BaseModel):
    model_config = OPEN_FOREIGN


class FollowupTaskArguments(BaseModel):
    model_config = OPEN_FOREIGN


CollaborationArguments: TypeAlias = Union[
    SendMessageArguments, SpawnAgentArguments, WaitAgentArguments,
    InterruptAgentArguments, ListAgentsArguments, FollowupTaskArguments,
]

COLLABORATION_ARGUMENTS: dict[str, type[CollaborationArguments]] = {
    "spawn_agent": SpawnAgentArguments,
    "wait_agent": WaitAgentArguments,
    "send_message": SendMessageArguments,
    "followup_task": FollowupTaskArguments,
    "interrupt_agent": InterruptAgentArguments,
    "list_agents": ListAgentsArguments,
}


# === OURS: one rollout RECORD per rollout.KINDS entry ========================
#
# Not pydantic: nothing foreign reaches a translator through these, they are
# built by the parsers above from already-validated payloads. Frozen,
# keyword-only, one dataclass per `kind` — the same discriminated-union shape
# domain/events.py's CanonicalEvent payloads already take, so a reader
# narrows on `record.kind` the same way it narrows on an EventPayload.


@dataclass(frozen=True, kw_only=True)
class PatchFile:
    path: str
    change: str | None
    added: int
    removed: int
    previous_path: str | None = None
    diff: str | None = None
    content: str | None = None


@dataclass(frozen=True, kw_only=True)
class AskQuestionRecord:
    id: str
    header: str
    question: str
    options: tuple[AskOptionRecord, ...]


@dataclass(frozen=True, kw_only=True)
class AskOptionRecord:
    label: str
    description: str


@dataclass(frozen=True, kw_only=True)
class TurnContextRecord:
    kind: Literal["turn_context"] = "turn_context"
    model: str
    effort: str


@dataclass(frozen=True, kw_only=True)
class UsageRecord:
    kind: Literal["usage"] = "usage"
    usage: TokenUsageBlock
    last: TokenUsageBlock | None
    window: int | None


@dataclass(frozen=True, kw_only=True)
class PatchRecord:
    kind: Literal["patch"] = "patch"
    success: bool
    files: tuple[PatchFile, ...]


@dataclass(frozen=True, kw_only=True)
class CompactRecord:
    kind: Literal["compact"] = "compact"


@dataclass(frozen=True, kw_only=True)
class TaskStartedRecord:
    kind: Literal["task_started"] = "task_started"
    at: str | int | float | None
    turn: str
    ts: str | None = None


@dataclass(frozen=True, kw_only=True)
class TaskCompleteRecord:
    kind: Literal["task_complete"] = "task_complete"
    at: str | int | float | None
    turn: str
    last: str
    ts: str | None = None


@dataclass(frozen=True, kw_only=True)
class TurnAbortedRecord:
    kind: Literal["turn_aborted"] = "turn_aborted"
    turn: str


@dataclass(frozen=True, kw_only=True)
class PromptRecord:
    kind: Literal["prompt"] = "prompt"
    text: str


@dataclass(frozen=True, kw_only=True)
class ReasoningRecord:
    kind: Literal["reasoning"] = "reasoning"
    text: str


@dataclass(frozen=True, kw_only=True)
class MessageRecord:
    kind: Literal["message"] = "message"
    text: str
    phase: str
    ts: str | None = None


@dataclass(frozen=True, kw_only=True)
class SearchRecord:
    kind: Literal["search"] = "search"
    query: str


@dataclass(frozen=True, kw_only=True)
class ExecRecord:
    kind: Literal["exec"] = "exec"
    cmd: str
    call_id: CallId
    ts: str | None = None


@dataclass(frozen=True, kw_only=True)
class ToolRecord:
    kind: Literal["tool"] = "tool"
    name: str
    args: str
    call_id: CallId


@dataclass(frozen=True, kw_only=True)
class ExecResultRecord:
    kind: Literal["exec_result"] = "exec_result"
    exit: str | int | None
    output: str
    call_id: CallId
    process_id: ShellNativeId | None = None
    running: bool = False
    ts: str | None = None


@dataclass(frozen=True, kw_only=True)
class StdinRecord:
    kind: Literal["stdin"] = "stdin"
    text: str
    call_id: CallId
    process_id: ShellNativeId


@dataclass(frozen=True, kw_only=True)
class CommandCompletedRecord:
    kind: Literal["command_completed"] = "command_completed"
    process_id: ShellNativeId
    output: str
    exit: int | None
    item_id: str


@dataclass(frozen=True, kw_only=True)
class ChatRecord:
    kind: Literal["chat"] = "chat"
    role: str
    text: str
    synthetic: bool
    phase: str
    turn: str


@dataclass(frozen=True, kw_only=True)
class ThinkRecord:
    kind: Literal["think"] = "think"
    text: str


@dataclass(frozen=True, kw_only=True)
class PatchCallRecord:
    kind: Literal["patch_call"] = "patch_call"
    patch: str
    call_id: CallId


@dataclass(frozen=True, kw_only=True)
class AskRecord:
    kind: Literal["ask"] = "ask"
    call_id: CallId
    questions: tuple[AskQuestionRecord, ...]


@dataclass(frozen=True, kw_only=True)
class PlanRecord:
    kind: Literal["plan"] = "plan"
    text: str
    id: str


@dataclass(frozen=True, kw_only=True)
class SettingsRecord:
    kind: Literal["settings"] = "settings"
    model: str
    effort: str


@dataclass(frozen=True, kw_only=True)
class CompactBoundaryRecord:
    kind: Literal["compact_boundary"] = "compact_boundary"
    message: str
    replaced: int
    window_id: str | int | None
    previous_window_id: str | int | None


@dataclass(frozen=True, kw_only=True)
class ActorActivityRecord:
    kind: Literal["actor_activity"] = "actor_activity"
    activity: str
    actor_id: ActorId
    actor_path: str
    call_id: CallId
    turn: str
    at: float | None


@dataclass(frozen=True, kw_only=True)
class CollaborationCallRecord:
    kind: Literal["collaboration_call"] = "collaboration_call"
    name: str
    args: CollaborationArguments
    call_id: CallId


@dataclass(frozen=True, kw_only=True)
class TaskListRecord:
    kind: Literal["task_list"] = "task_list"
    tasks: tuple[PlanTask, ...]
    call_id: CallId


@dataclass(frozen=True, kw_only=True)
class GoalRecord:
    kind: Literal["goal"] = "goal"
    objective: str | None
    status: str | None
    reason: str | None


@dataclass(frozen=True, kw_only=True)
class GoalToolRecord:
    kind: Literal["goal_tool"] = "goal_tool"
    call_id: CallId


@dataclass(frozen=True, kw_only=True)
class UnmappedToolRecord:
    kind: Literal["unmapped_tool"] = "unmapped_tool"
    name: str


@dataclass(frozen=True, kw_only=True)
class BadRecord:
    kind: Literal["bad"] = "bad"
    raw: str


@dataclass(frozen=True, kw_only=True)
class WorldStateRecord:
    kind: Literal["world_state"] = "world_state"


@dataclass(frozen=True, kw_only=True)
class CoveredItemRecord:
    kind: Literal["covered_item"] = "covered_item"


@dataclass(frozen=True, kw_only=True)
class EmptyRecord:
    kind: Literal["empty"] = "empty"


# The COMPLETE set of records parse()/parse_line() can return — the typed twin
# of rollout.KINDS (which stays, as the string vocabulary the render/ignore
# pin in the test suite checks against).
RolloutRecord: TypeAlias = Union[
    TurnContextRecord, UsageRecord, PatchRecord, CompactRecord, TaskStartedRecord,
    TaskCompleteRecord, TurnAbortedRecord, PromptRecord, ReasoningRecord, MessageRecord,
    SearchRecord, ExecRecord, ExecResultRecord, StdinRecord, CommandCompletedRecord,
    ChatRecord, ThinkRecord, PatchCallRecord, AskRecord, PlanRecord, SettingsRecord,
    CompactBoundaryRecord, ToolRecord, ActorActivityRecord, CollaborationCallRecord,
    TaskListRecord, GoalRecord, GoalToolRecord, UnmappedToolRecord, BadRecord,
    WorldStateRecord, CoveredItemRecord, EmptyRecord,
]
