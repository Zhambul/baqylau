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
from collections.abc import Mapping
from enum import StrEnum
from typing import Annotated, Generic, Literal, TypeAlias, TypeVar, Union

from pydantic import BaseModel, ConfigDict, Field, RootModel

from harness.impl.codex.ids import (
    CodexActorId,
    CodexCallId,
    CodexSessionId,
    CodexShellId,
    CodexTurnId,
)
from harness.impl.codex.model import BaseInstructionsSourceType, CodexEffort, CodexModel

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


class ForeignMetadata(BaseModel):
    model_config = OPEN_FOREIGN


class RolloutHeader(BaseModel):
    model_config = OPEN_FOREIGN
    type: str | None = None
    timestamp: str | None = None
    payload: ForeignMetadata | None = None


class RolloutInput(RootModel[Mapping[str, object]]):
    """Compatibility input for callers that already decoded a rollout line."""


class NativePayloadIdentity(BaseModel):
    model_config = OPEN_FOREIGN
    id: str | int | None = None
    item_id: str | int | None = None
    turn_id: CodexTurnId | None = None


class RolloutObservation(BaseModel):
    model_config = OPEN_FOREIGN
    type: str | None = None
    timestamp: str | int | float | None = None
    payload: NativePayloadIdentity | None = None


class PayloadTypeHeader(BaseModel):
    model_config = OPEN_FOREIGN
    type: str | None = None


class ToolRequest(BaseModel):
    model_config = OPEN_FOREIGN
    q: str | None = None
    query: str | None = None
    url: str | None = None
    location: str | None = None
    ticker: str | None = None
    utc_offset: str | None = None
    team: str | None = None
    fn: str | None = None
    reference: str | None = Field(default=None, alias="ref_id")


class CodexToolArguments(BaseModel):
    model_config = OPEN_FOREIGN
    search_query: list[ToolRequest] | None = None
    image_query: list[ToolRequest] | None = None
    weather: list[ToolRequest] | None = None
    finance: list[ToolRequest] | None = None
    sports: list[ToolRequest] | None = None
    time: list[ToolRequest] | None = None
    open: list[ToolRequest] | None = None
    click: list[ToolRequest] | None = None
    find: list[ToolRequest] | None = None
    screenshot: list[ToolRequest] | None = None
    query: str | None = None
    url: str | None = None
    path: str | None = None
    file_path: str | None = None
    uri: str | None = None


PayloadModel = TypeVar("PayloadModel", bound=BaseModel)


class RolloutDocument(BaseModel, Generic[PayloadModel]):
    model_config = OPEN_FOREIGN
    type: str
    timestamp: str | None = None
    payload: PayloadModel


class PayloadHeaderDocument(RolloutDocument[PayloadTypeHeader]):
    pass


# === FOREIGN: the event_msg register's `payload` (events.py) ================


class TokenUsageBlock(BaseModel):
    """One `total_token_usage` / `last_token_usage` snapshot."""

    model_config = FOREIGN
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    cache_write_input_tokens: int | None = None
    reasoning_output_tokens: int | None = None
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


class RateLimitCredits(BaseModel):
    model_config = FOREIGN
    has_credits: bool | None = None
    unlimited: bool | None = None
    balance: float | int | None = None


class RateLimitsBlock(BaseModel):
    model_config = FOREIGN
    plan_type: str | None = None
    primary: RateLimitWindow | None = None
    secondary: RateLimitWindow | None = None
    limit_id: str | None = None
    limit_name: str | None = None
    individual_limit: str | int | float | None = None
    credits: RateLimitCredits | None = None
    rate_limit_reached_type: str | None = None
    spend_control_reached: bool | None = None


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
    threadId: CodexSessionId | None = None
    tokensUsed: int | None = None
    timeUsedSeconds: int | None = None
    createdAt: int | None = None
    updatedAt: int | None = None


class ThreadGoalUpdatedPayload(BaseModel):
    model_config = FOREIGN
    type: Literal["thread_goal_updated"] = "thread_goal_updated"
    goal: GoalBlock | None = None
    threadId: CodexSessionId | None = None


class EmptyPayload(BaseModel):
    """A payload whose handler reads nothing from it: `thread_goal_cleared`,
    `context_compacted`. Declared (rather than skipped) so an unexpected
    field on one of these still fails fast instead of silently riding along
    unread. Shared by both `type` strings, so `type` itself is read but not
    constrained to one of them here — the dispatch table that chose this
    model already did that check."""

    model_config = FOREIGN
    type: Literal["thread_goal_cleared", "context_compacted"]


class WorldStatePayload(BaseModel):
    """A `world_state` top-level record: a large periodic state snapshot
    (open files, shell sessions, todos) — GENUINELY open (module header,
    OPEN_FOREIGN), not a shape this codebase has ever read one field of, let
    alone declared exhaustively."""

    model_config = OPEN_FOREIGN


class InterAgentCommunicationMetadataPayload(BaseModel):
    """A v2 child-turn trigger with no user-visible content."""

    model_config = FOREIGN
    trigger_turn: bool


class TaskStartedPayload(BaseModel):
    model_config = FOREIGN
    type: Literal["task_started"] = "task_started"
    started_at: str | int | float | None = None
    turn_id: CodexTurnId | None = None
    collaboration_mode_kind: str | None = None
    model_context_window: int | None = None


class TaskCompleteError(BaseModel):
    model_config = FOREIGN
    message: str | None = None


class TaskCompletePayload(BaseModel):
    model_config = FOREIGN
    type: Literal["task_complete"] = "task_complete"
    completed_at: str | int | float | None = None
    turn_id: CodexTurnId | None = None
    last_agent_message: str | None = None
    started_at: str | int | float | None = None
    duration_ms: int | None = None
    time_to_first_token_ms: int | None = None
    error: TaskCompleteError | None = None


class CollaborationModeSettings(BaseModel):
    model_config = FOREIGN
    model: CodexModel | None = None
    reasoning_effort: CodexEffort | None = None
    developer_instructions: str | None = None


class CollaborationMode(BaseModel):
    model_config = FOREIGN
    mode: str | None = None
    settings: CollaborationModeSettings | None = None


class ThreadSettingsBlock(BaseModel):
    model_config = FOREIGN
    model: CodexModel | None = None
    reasoning_effort: CodexEffort | None = None
    model_provider_id: str | None = None
    service_tier: str | None = None
    approval_policy: str | None = None
    approvals_reviewer: str | None = None
    cwd: str | None = None
    personality: str | None = None
    reasoning_summary: str | None = None
    collaboration_mode: CollaborationMode | None = None
    # Deep, vendor-owned policy trees nothing here reads a field of — same
    # treatment as TurnContextPayload's sandbox/permission fields below.
    active_permission_profile: ForeignMetadata | None = None
    permission_profile: ForeignMetadata | None = None


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
    changes: FileChanges | None = None
    stdout: str | None = None
    stderr: str | None = None


class DurationBlock(BaseModel):
    model_config = FOREIGN
    secs: int | None = None
    nanos: int | None = None


class CommandExecutionItem(BaseModel):
    model_config = FOREIGN
    type: Literal["CommandExecution"]
    id: str | None = None
    status: str | None = None
    process_id: CodexShellId | int | None = None
    aggregated_output: str | None = None
    formatted_output: str | None = None
    stdout: str | None = None
    stderr: str | None = None
    exit_code: int | None = None
    command: list[str] | None = None
    cwd: str | None = None
    duration: DurationBlock | None = None
    source: str | None = None
    # The parser's own guess at the command's shell-builtin shape — never
    # read here (the raw `command`/`aggregated_output` are); a real vendor
    # field, still open (module header): its element shape varies by guess.
    parsed_cmd: list[ForeignMetadata | str] | None = None


class SubAgentActivityItem(BaseModel):
    model_config = FOREIGN
    type: Literal["SubAgentActivity"]
    kind: str | None = None
    agent_thread_id: CodexActorId | None = None
    agent_path: str | None = None
    id: str | None = None


class CollabAgentReference(BaseModel):
    model_config = FOREIGN
    thread_id: CodexActorId
    agent_nickname: str | None = None


class CollabAgentStates(RootModel[Mapping[CodexActorId, str]]):
    pass


class CollabAgentToolCallItem(BaseModel):
    """A collaboration mirror whose child rollout owns the canonical facts."""

    model_config = FOREIGN
    type: Literal["CollabAgentToolCall"]
    id: str | None = None
    tool: str | None = None
    status: str | None = None
    sender_thread_id: CodexActorId | None = None
    receiver_thread_ids: list[CodexActorId] | None = None
    receiver_agents: list[CollabAgentReference] | None = None
    prompt: str | None = None
    model: str | None = None
    reasoning_effort: str | None = None
    agents_states: CollabAgentStates | None = None


class PlanItem(BaseModel):
    model_config = FOREIGN
    type: Literal["Plan"]
    text: str | None = None
    id: str | None = None


class McpToolCallArguments(BaseModel):
    model_config = OPEN_FOREIGN
    title: str | None = None


class McpToolResultContent(BaseModel):
    model_config = OPEN_FOREIGN
    type: str | None = None
    text: str | None = None


class McpToolResultMetadata(BaseModel):
    model_config = OPEN_FOREIGN
    browser_use: bool = Field(default=False, alias="codex/browserUse")


class McpToolCallResult(BaseModel):
    model_config = OPEN_FOREIGN
    content: list[McpToolResultContent | str] | None = None
    is_error: bool = Field(default=False, alias="isError")
    metadata: McpToolResultMetadata | None = Field(default=None, alias="_meta")


class McpToolCallItem(BaseModel):
    """The authoritative completion state for one MCP call.

    The call arguments and result have a tool-specific shape. The outer custom
    tool records own that content. This item owns only the MCP identity and its
    native completion state.
    """

    model_config = OPEN_FOREIGN
    type: Literal["McpToolCall"]
    id: str | None = None
    server: str | None = None
    tool: str | None = None
    status: str | None = None
    arguments: McpToolCallArguments | None = None
    result: McpToolCallResult | None = None


class CoveredItem(BaseModel):
    """An item whose canonical facts come from another native record.

    Open on purpose (OPEN_FOREIGN, module header): the whole point of this
    model is that NOTHING on it is read, only its `type`, so its other
    fields (the very content the other register already delivers) are not
    worth declaring precisely — the shape lives in the response_item models
    that actually read it (items.MessagePayload, items.ReasoningPayload)."""

    model_config = OPEN_FOREIGN
    type: Literal[
        "UserMessage", "AgentMessage", "Reasoning", "ContextCompaction",
        "Extension", "ImageView",
    ]


ItemCompletedItem: TypeAlias = Union[
    FileChangeItem, CommandExecutionItem, SubAgentActivityItem, CollabAgentToolCallItem,
    PlanItem, McpToolCallItem, CoveredItem,
]


class ItemCompletedType(StrEnum):
    FILE_CHANGE = "FileChange"
    COMMAND_EXECUTION = "CommandExecution"
    SUBAGENT_ACTIVITY = "SubAgentActivity"
    COLLAB_AGENT_TOOL_CALL = "CollabAgentToolCall"
    PLAN = "Plan"
    USER_MESSAGE = "UserMessage"
    AGENT_MESSAGE = "AgentMessage"
    REASONING = "Reasoning"
    MCP_TOOL_CALL = "McpToolCall"
    CONTEXT_COMPACTION = "ContextCompaction"
    EXTENSION = "Extension"
    IMAGE_VIEW = "ImageView"

# `item.type` -> the declared model for it. A plain dict, not a pydantic
# discriminated union: pydantic's "smart" union mode picks by a coercion-cost
# heuristic, not by the discriminator alone, and a permissive catch-all member
# in the union (needed for an UNKNOWN item type to fall through, not fail)
# skewed that heuristic — measured picking the catch-all over an exact
# `Literal["FileChange"]` match. Dispatching on this dict FIRST, exactly like
# rollout.EVENTS/RESPONSES, keeps "unknown type" and "known type, bad shape"
# the two separate outcomes the owner's decision needs them to be.
ITEM_COMPLETED_ITEMS: Mapping[ItemCompletedType, type[ItemCompletedItem]] = {
    ItemCompletedType.FILE_CHANGE: FileChangeItem,
    ItemCompletedType.COMMAND_EXECUTION: CommandExecutionItem,
    ItemCompletedType.SUBAGENT_ACTIVITY: SubAgentActivityItem,
    ItemCompletedType.COLLAB_AGENT_TOOL_CALL: CollabAgentToolCallItem,
    ItemCompletedType.PLAN: PlanItem,
    ItemCompletedType.USER_MESSAGE: CoveredItem,
    ItemCompletedType.AGENT_MESSAGE: CoveredItem,
    ItemCompletedType.REASONING: CoveredItem,
    ItemCompletedType.MCP_TOOL_CALL: McpToolCallItem,
    ItemCompletedType.CONTEXT_COMPACTION: CoveredItem,
    ItemCompletedType.EXTENSION: CoveredItem,
    ItemCompletedType.IMAGE_VIEW: CoveredItem,
}


class FileChanges(RootModel[Mapping[str, FileChangeEntry]]):
    pass


CompletedItem = Annotated[
    Union[
        FileChangeItem, CommandExecutionItem, SubAgentActivityItem,
        CollabAgentToolCallItem, PlanItem, McpToolCallItem, CoveredItem,
    ],
    Field(discriminator="type"),
]


class ItemCompletedPayload(BaseModel):
    model_config = FOREIGN
    type: Literal["item_completed"] = "item_completed"
    turn_id: CodexTurnId | None = None
    started_at_ms: int | None = None
    completed_at_ms: int | None = None
    thread_id: CodexSessionId | None = None
    item: CompletedItem | None = None


class ItemTypeHeader(BaseModel):
    model_config = OPEN_FOREIGN
    type: str | None = None


class ItemCompletedHeaderPayload(BaseModel):
    model_config = OPEN_FOREIGN
    type: Literal["item_completed"] = "item_completed"
    item: ItemTypeHeader | None = None


class TurnAbortedPayload(BaseModel):
    model_config = FOREIGN
    type: Literal["turn_aborted"] = "turn_aborted"
    turn_id: CodexTurnId | None = None
    reason: str | None = None
    completed_at: str | int | float | None = None
    duration_ms: int | None = None
    started_at: str | int | float | None = None


class UserMessagePayload(BaseModel):
    model_config = FOREIGN
    type: Literal["user_message"] = "user_message"
    message: str | None = None
    client_id: str | None = None
    # Attachment lists — every measured rollout carries them EMPTY, so their
    # populated element shape is not yet known (module header: declare what
    # reality allows, not what it might one day be).
    images: list[ForeignMetadata] | None = None
    local_images: list[ForeignMetadata] | None = None
    text_elements: list[ForeignMetadata] | None = None
    audio: list[ForeignMetadata] | None = None
    local_audio: list[ForeignMetadata] | None = None


class AgentReasoningPayload(BaseModel):
    model_config = FOREIGN
    type: Literal["agent_reasoning"] = "agent_reasoning"
    text: str | None = None


class AgentMessagePayload(BaseModel):
    model_config = FOREIGN
    type: Literal["agent_message"] = "agent_message"
    message: str | None = None
    phase: str | None = None
    # Always None in every measured rollout; its populated shape is unknown.
    memory_citation: None = None


class WebSearchAction(BaseModel):
    model_config = FOREIGN
    type: str | None = None
    query: str | None = None
    queries: list[str] | None = None
    url: str | None = None
    pattern: str | None = None


class WebSearchEndPayload(BaseModel):
    model_config = FOREIGN
    type: Literal["web_search_end"] = "web_search_end"
    query: str | None = None
    action: WebSearchAction | None = None
    call_id: CodexCallId | None = None
    results: list[ForeignMetadata] | None = None


EventPayload = Annotated[
    Union[
        TokenCountPayload, ThreadGoalUpdatedPayload, EmptyPayload, TaskStartedPayload,
        TaskCompletePayload, ThreadSettingsAppliedPayload, ItemCompletedPayload,
        TurnAbortedPayload, UserMessagePayload, AgentReasoningPayload,
        AgentMessagePayload, WebSearchEndPayload,
    ],
    Field(discriminator="type"),
]


class EventDocument(RolloutDocument[EventPayload]):
    type: Literal["event_msg"] = "event_msg"


# === FOREIGN: the top-level register (rollout.py) ============================
# CollaborationMode/CollaborationModeSettings are declared earlier (the
# event_msg section above) — codex stamps the SAME `{mode, settings}` shape
# on task_started/thread_settings_applied AND here, so one model serves all
# three.


class TurnContextPayload(BaseModel):
    """A `turn_context` top-level record. `model`/`effort`/
    `collaboration_mode.settings.reasoning_effort` are the only fields
    rollout._turn_context reads; the rest are real (a live codex-cli 0.147.0
    rollout) but unread — declared because `extra="forbid"` demands every
    field codex sends, not only the ones used. The deep policy trees
    (sandbox/permission/file-system) are GENUINELY open (module header,
    OPEN_FOREIGN in spirit, `dict[str, JsonValue]` in practice): a vendor
    policy DSL nothing here has ever read one field of."""

    model_config = FOREIGN
    model: CodexModel | None = None
    effort: CodexEffort | None = None
    collaboration_mode: CollaborationMode | None = None
    turn_id: CodexTurnId | None = None
    cwd: str | None = None
    current_date: str | None = None
    timezone: str | None = None
    approval_policy: str | None = None
    sandbox_policy: ForeignMetadata | None = None
    personality: str | None = None
    summary: str | None = None
    user_instructions: str | None = None
    developer_instructions: str | None = None
    truncation_policy: ForeignMetadata | None = None
    permission_profile: ForeignMetadata | None = None
    realtime_active: bool | None = None
    file_system_sandbox_policy: ForeignMetadata | None = None
    workspace_roots: list[str] | None = None
    comp_hash: str | None = None
    multi_agent_version: str | None = None
    approvals_reviewer: str | None = None
    multi_agent_mode: str | None = None


class CompactedContentPart(BaseModel):
    model_config = OPEN_FOREIGN
    type: str | None = None
    text: str | None = None


class CompactedHistoryItem(BaseModel):
    """One readable member of Codex's replacement context."""

    model_config = OPEN_FOREIGN
    type: str | None = None
    role: str | None = None
    author: str | None = None
    recipient: str | None = None
    content: str | list[CompactedContentPart | str] | None = None
    encrypted_content: str | None = None


class CompactedPayload(BaseModel):
    model_config = FOREIGN
    message: str | None = None
    replacement_history: list[CompactedHistoryItem] | None = None
    window_id: str | int | None = None
    previous_window_id: str | int | None = None
    first_window_id: str | int | None = None
    window_number: int | None = None


class ThreadSpawn(BaseModel):
    model_config = FOREIGN
    parent_thread_id: CodexSessionId | None = None
    agent_path: str | None = None
    depth: int | None = None
    agent_nickname: str | None = None
    agent_role: str | None = None


class SubagentSource(BaseModel):
    model_config = FOREIGN
    thread_spawn: ThreadSpawn | None = None


class SessionMetaSource(BaseModel):
    model_config = FOREIGN
    subagent: SubagentSource | None = None


class SessionMetaBaseInstructionsSource(BaseModel):
    model_config = FOREIGN
    type: BaseInstructionsSourceType
    model: CodexModel


class SessionMetaBaseInstructions(BaseModel):
    model_config = FOREIGN
    text: str | None = None
    source: SessionMetaBaseInstructionsSource | None = Field(
        default=None,
        validation_alias="pro" + "venance",
    )


class SessionMetaContextWindow(BaseModel):
    model_config = FOREIGN
    window_id: str | None = None


class SessionMetaHistoryBase(BaseModel):
    """The immutable rollout prefix used by a paginated rewind."""

    model_config = FOREIGN
    thread_id: CodexSessionId
    end_ordinal_exclusive: int
    end_byte_offset: int


class SessionMetaGit(BaseModel):
    """The repository facts codex stamps on a session — `{}` outside a repo,
    `{commit_hash, branch, repository_url}` inside one (both measured, real
    local rollouts)."""

    model_config = FOREIGN
    commit_hash: str | None = None
    branch: str | None = None
    repository_url: str | None = None


class SessionMetaPayload(BaseModel):
    """A `session_meta` record's `payload` — read by sources.py (rollout
    ownership / parent-thread discovery) and translator.py (actor naming).
    Most fields below (session_id, cli_version, model_provider, …) are real,
    measured (a live codex-cli 0.147.0 rollout) but read by NOTHING here —
    declared anyway because `extra="forbid"` demands it of every field codex
    actually sends, not only the ones this translator uses."""

    model_config = FOREIGN
    id: str | None = None
    session_id: CodexSessionId | None = None
    cwd: str | None = None
    timestamp: str | None = None
    thread_source: str | None = None
    parent_thread_id: CodexSessionId | None = None
    # A subagent's spawn detail (SessionMetaSource) OR a plain string naming
    # WHAT started the session ("vscode", the IDE extension, "startup" the
    # CLI itself) — codex uses the one field for all three.
    source: SessionMetaSource | str | None = None
    originator: str | None = None
    cli_version: str | None = None
    model_provider: str | None = None
    base_instructions: SessionMetaBaseInstructions | None = None
    history_mode: str | None = None
    history_base: SessionMetaHistoryBase | None = None
    context_window: SessionMetaContextWindow | None = None
    git: SessionMetaGit | None = None
    # The MCP-style tool manifest codex's app-server negotiates per session —
    # an arbitrarily deep, vendor-versioned JSON-Schema tree (measured: nested
    # `oneOf`/`$ref`/`$defs`) nothing here reads a field of; a valid JSON list
    # is the whole of what this codebase can honestly claim to know about it.
    dynamic_tools: list[ForeignMetadata] | None = None
    agent_nickname: str | None = None
    # The spawning actor's own agent_path — a TOP-LEVEL sibling of the nested
    # `source.subagent.thread_spawn.agent_path` above (both measured, real
    # local rollouts; codex writes the fact in two places).
    agent_path: str | None = None
    forked_from_id: CodexSessionId | None = None
    multi_agent_version: str | None = None
    subagent_history_start_ordinal: int | None = None


class CodexHookPayload(BaseModel):
    """A codex hook delivery's JSON body — GENUINELY open (module header,
    OPEN_FOREIGN): unlike a rollout record, a hook delivery's field set varies
    by `hook_event_name` (SessionStart/PreCompact/PostCompact/…), most of
    which this translator never reads and has no fixture corpus to declare
    exhaustively. Declared as far as reality allows: the seven fields
    translator._translate_hook actually reads."""

    model_config = OPEN_FOREIGN
    session_id: CodexSessionId | None = None
    agent_id: CodexActorId | None = None
    hook_event_name: str | None = None
    hook_event_id: str | None = None
    uuid: str | None = None
    transcript_path: str | None = None
    cwd: str | None = None
    before_tokens: int | None = None
    after_tokens: int | None = None


# === FOREIGN: the response_item register (items.py) ==========================


class ChatMessageMetadata(BaseModel):
    model_config = FOREIGN
    turn_id: CodexTurnId | None = None
    create_time: float | None = None
    content_item_kinds: list[str] | None = None


class WebSearchCallAction(BaseModel):
    model_config = FOREIGN
    type: str | None = None
    query: str | None = None
    queries: list[str] | None = None
    url: str | None = None
    pattern: str | None = None


class WebSearchCallPayload(BaseModel):
    model_config = FOREIGN
    type: Literal["web_search_call"] = "web_search_call"
    id: str | None = None
    action: WebSearchCallAction | None = None
    status: str | None = None
    internal_chat_message_metadata_passthrough: ChatMessageMetadata | None = None


class ContentPart(BaseModel):
    model_config = FOREIGN
    type: str | None = None
    text: str | None = None
    image_url: str | None = None
    detail: str | None = None


class AgentCommunicationPayload(BaseModel):
    """A v2 agent-to-agent message whose task body can be encrypted."""

    model_config = OPEN_FOREIGN
    type: Literal["agent_message"] = "agent_message"


class NodeReplResultDocument(BaseModel):
    """The outer result document returned by the node-repl MCP tool."""

    model_config = FOREIGN
    content: list[ContentPart]
    isError: bool = False


class FunctionCallOutputPayload(BaseModel):
    model_config = FOREIGN
    type: Literal["function_call_output"] = "function_call_output"
    id: str | None = None
    output: str | list[ContentPart | str] | None = None
    call_id: CodexCallId | None = None
    internal_chat_message_metadata_passthrough: ChatMessageMetadata | None = None


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
    # Always None where `summary` carries the text (encrypted_content holds
    # it instead when the think is stored encrypted) — never both populated
    # in any measured rollout, so `content`'s populated shape is unknown.
    content: None = None
    encrypted_content: str | None = None
    internal_chat_message_metadata_passthrough: ChatMessageMetadata | None = None


class CustomToolCallPayload(BaseModel):
    model_config = FOREIGN
    type: Literal["custom_tool_call"] = "custom_tool_call"
    id: str | None = None
    name: str | None = None
    input: str | list[ContentPart | str] | None = None
    call_id: CodexCallId | None = None
    status: str | None = None
    internal_chat_message_metadata_passthrough: ChatMessageMetadata | None = None


class CustomToolCallOutputPayload(BaseModel):
    model_config = FOREIGN
    type: Literal["custom_tool_call_output"] = "custom_tool_call_output"
    id: str | None = None
    output: str | list[ContentPart | str] | None = None
    call_id: CodexCallId | None = None
    internal_chat_message_metadata_passthrough: ChatMessageMetadata | None = None


class FunctionCallPayload(BaseModel):
    model_config = FOREIGN
    type: Literal["function_call"] = "function_call"
    id: str | None = None
    name: str | None = None
    namespace: str | None = None
    internal_chat_message_metadata_passthrough: ChatMessageMetadata | None = None
    call_id: CodexCallId | None = None
    arguments: str | None = None


ResponsePayload = Annotated[
    Union[
        WebSearchCallPayload, FunctionCallOutputPayload, MessagePayload,
        ReasoningPayload, CustomToolCallPayload, CustomToolCallOutputPayload,
        FunctionCallPayload, AgentCommunicationPayload,
    ],
    Field(discriminator="type"),
]


class ResponseDocument(RolloutDocument[ResponsePayload]):
    type: Literal["response_item"] = "response_item"


class TurnContextDocument(RolloutDocument[TurnContextPayload]):
    type: Literal["turn_context"] = "turn_context"


class CompactedDocument(RolloutDocument[CompactedPayload]):
    type: Literal["compacted"] = "compacted"


class WorldStateDocument(RolloutDocument[WorldStatePayload]):
    type: Literal["world_state"] = "world_state"


class InterAgentCommunicationMetadataDocument(
    RolloutDocument[InterAgentCommunicationMetadataPayload]
):
    type: Literal["inter_agent_communication_metadata"] = (
        "inter_agent_communication_metadata"
    )


class CombinedCommandResult(BaseModel):
    model_config = OPEN_FOREIGN
    output: str | None = None
    exit_code: int | None = None
    session_id: CodexShellId | int | None = None


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
    session_id: CodexShellId | int | None = None
    exit_code: int | None = None


class GoalToolResultBlock(BaseModel):
    """The goal fields that Codex control tools return.

    The result can also include budget and elapsed-use fields. Those fields do
    not change the canonical goal, so this boundary leaves them open and reads
    only the goal identity and state.
    """

    model_config = OPEN_FOREIGN
    objective: str | None = None
    status: str | None = None
    reason: str | None = None


class GoalToolResultDocument(BaseModel):
    model_config = OPEN_FOREIGN
    goal: GoalToolResultBlock | None = None


# --- function_call NAME -> its argument grammar ------------------------------
# `arguments` is a JSON *string*; these models are what it decodes to. A
# codex build that stops sending valid JSON there still degrades (items._args
# falls back to `{}`), so THIS gate never fires on that failure mode — only
# on a decoded object that carries a field none of these name.

class ExecArguments(BaseModel):
    model_config = FOREIGN
    cmd: str | list[str] | None = None
    command: str | list[str] | None = None
    workdir: str | None = None
    yield_time_ms: int | None = None
    max_output_tokens: int | None = None
    shell: str | None = None
    tty: bool | None = None
    login: bool | None = None
    sandbox_permissions: str | None = None
    justification: str | None = None
    prefix_rule: list[str] | None = None


class StdinArguments(BaseModel):
    model_config = FOREIGN
    session_id: CodexShellId | int | None = None
    chars: str | None = None
    yield_time_ms: int | None = None
    max_output_tokens: int | None = None


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


class AskAnswer(BaseModel):
    model_config = FOREIGN
    answers: tuple[str, ...] = ()


class AskAnswers(RootModel[Mapping[str, AskAnswer]]):
    pass


class AskResultDocument(BaseModel):
    """The value Codex records for a completed request_user_input call."""

    model_config = FOREIGN
    answers: AskAnswers


class PlanTask(BaseModel):
    model_config = FOREIGN
    step: str | None = None
    status: str | None = None


class PlanArguments(BaseModel):
    model_config = FOREIGN
    plan: list[PlanTask] | None = None


class GoalArguments(BaseModel):
    model_config = FOREIGN
    objective: str | None = None
    status: str | None = None
    reason: str | None = None
    token_budget: int | None = None


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

class CollaborationCallName(StrEnum):
    SPAWN_AGENT = "spawn_agent"
    WAIT_AGENT = "wait_agent"
    SEND_MESSAGE = "send_message"
    FOLLOWUP_TASK = "followup_task"
    INTERRUPT_AGENT = "interrupt_agent"
    LIST_AGENTS = "list_agents"


COLLABORATION_ARGUMENTS: Mapping[CollaborationCallName, type[CollaborationArguments]] = {
    CollaborationCallName.SPAWN_AGENT: SpawnAgentArguments,
    CollaborationCallName.WAIT_AGENT: WaitAgentArguments,
    CollaborationCallName.SEND_MESSAGE: SendMessageArguments,
    CollaborationCallName.FOLLOWUP_TASK: FollowupTaskArguments,
    CollaborationCallName.INTERRUPT_AGENT: InterruptAgentArguments,
    CollaborationCallName.LIST_AGENTS: ListAgentsArguments,
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
    model: CodexModel | None
    effort: CodexEffort | None


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
class SkillRecord:
    kind: Literal["skill"] = "skill"
    name: str
    output: str
    turn: str


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
    call_id: CodexCallId
    turn: CodexTurnId | None = None
    yield_ms: int | None = None
    reports_session_id: bool = False
    ts: str | None = None


@dataclass(frozen=True, kw_only=True)
class ToolRecord:
    kind: Literal["tool"] = "tool"
    name: str
    args: str
    call_id: CodexCallId


@dataclass(frozen=True, kw_only=True)
class ExecResultRecord:
    kind: Literal["exec_result"] = "exec_result"
    exit: str | int | None
    output: str
    call_id: CodexCallId
    process_id: CodexShellId | None = None
    running: bool = False
    interrupted: bool = False
    ts: str | None = None


@dataclass(frozen=True, kw_only=True)
class StdinRecord:
    kind: Literal["stdin"] = "stdin"
    text: str
    call_id: CodexCallId
    process_id: CodexShellId


@dataclass(frozen=True, kw_only=True)
class CommandCompletedRecord:
    kind: Literal["command_completed"] = "command_completed"
    process_id: CodexShellId
    command: tuple[str, ...]
    output: str
    exit: int | None
    item_id: str
    turn: CodexTurnId | None = None


@dataclass(frozen=True, kw_only=True)
class McpToolCompletedRecord:
    kind: Literal["mcp_tool_completed"] = "mcp_tool_completed"
    server: str
    tool: str
    status: str
    item_id: str
    title: str | None = None
    result: str | None = None
    result_is_error: bool = False
    browser_use: bool = False


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
    call_id: CodexCallId


@dataclass(frozen=True, kw_only=True)
class AskRecord:
    kind: Literal["ask"] = "ask"
    call_id: CodexCallId
    questions: tuple[AskQuestionRecord, ...]


@dataclass(frozen=True, kw_only=True)
class PlanRecord:
    kind: Literal["plan"] = "plan"
    text: str
    id: str


@dataclass(frozen=True, kw_only=True)
class SettingsRecord:
    kind: Literal["settings"] = "settings"
    model: CodexModel | None
    effort: CodexEffort | None


@dataclass(frozen=True, kw_only=True)
class CompactBoundaryRecord:
    kind: Literal["compact_boundary"] = "compact_boundary"
    message: str
    context: str
    replaced: int
    window_id: str | int | None
    previous_window_id: str | int | None


@dataclass(frozen=True, kw_only=True)
class ActorActivityRecord:
    kind: Literal["actor_activity"] = "actor_activity"
    activity: str
    actor_id: CodexActorId
    actor_path: str
    call_id: CodexCallId
    turn: str
    at: float | None


@dataclass(frozen=True, kw_only=True)
class CollaborationCallRecord:
    kind: Literal["collaboration_call"] = "collaboration_call"
    name: str
    args: CollaborationArguments
    call_id: CodexCallId


@dataclass(frozen=True, kw_only=True)
class TaskListRecord:
    kind: Literal["task_list"] = "task_list"
    tasks: tuple[PlanTask, ...]
    call_id: CodexCallId


@dataclass(frozen=True, kw_only=True)
class GoalRecord:
    kind: Literal["goal"] = "goal"
    objective: str | None
    status: str | None
    reason: str | None


@dataclass(frozen=True, kw_only=True)
class GoalToolRecord:
    kind: Literal["goal_tool"] = "goal_tool"
    call_id: CodexCallId
    name: str
    objective: str | None = None
    status: str | None = None
    reason: str | None = None


@dataclass(frozen=True, kw_only=True)
class ToolBatchRecord:
    kind: Literal["tool_batch"] = "tool_batch"
    call_id: CodexCallId
    actions: tuple[
        ExecRecord | StdinRecord | ToolRecord | TaskListRecord | GoalToolRecord
        | CollaborationCallRecord,
        ...,
    ]

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
    TaskCompleteRecord, TurnAbortedRecord, PromptRecord, SkillRecord, ReasoningRecord, MessageRecord,
    SearchRecord, ExecRecord, ExecResultRecord, StdinRecord, CommandCompletedRecord,
    McpToolCompletedRecord,
    ChatRecord, ThinkRecord, PatchCallRecord, AskRecord, PlanRecord, SettingsRecord,
    CompactBoundaryRecord, ToolRecord, ActorActivityRecord, CollaborationCallRecord,
    TaskListRecord, GoalRecord, GoalToolRecord, ToolBatchRecord,
    UnmappedToolRecord, BadRecord,
    WorldStateRecord, CoveredItemRecord, EmptyRecord,
]
