# harness/impl/claude_code/canonical/records.py — Claude Code's DECLARED
# foreign shapes: the transcript JSONL record, the hook delivery, the OTLP
# metrics document, and the free-form tool call bodies riding inside them.
#
# Same two-tier scheme codex/canonical/records.py uses:
#
#   FOREIGN (`extra="forbid"`) is Claude Code's OWN, closed, fully-observed
#   shape: every field below a FOREIGN model declares is transcribed from a
#   real corpus read (~/.claude/projects/**/*.jsonl, plus this machine's own
#   raw_events table for hook/otel deliveries that never touch disk) or from
#   what the code already read before this module existed — never a guessed
#   field. A payload that does not match — missing, wrong type, or an EXTRA
#   field Claude Code did not used to send — raises `pydantic.ValidationError`,
#   which the interpreter loop (engine/interpret/loop.py) turns into the
#   `translation_failed` verdict naming the error. Owner's decision (TASKS.md):
#   a foreign record either matches exactly what we declared, or translation
#   stops until the new field is declared.
#
#   OPEN_FOREIGN (`extra="ignore"`) is for a shape that is GENUINELY open by
#   the vendor's own contract, not by an oversight of ours: a tool call's own
#   arguments/response, whose exact key set is chosen by whatever Claude Code
#   built-in or MCP tool answered, most of which nothing here reads. Declared
#   as far as reality allows — the fields something in this package actually
#   reads, plus what a real corpus sample showed for the better-travelled
#   tools — with every OTHER field riding along unread rather than failing the
#   record for it.
#
# The RECORD DISPATCH stays a plain dict lookup on `type` (transcript.py's
# `if t == "user":` chain, hooks.py's `if hook_name == "Stop":` chain, TOOL_KINDS
# in toolcalls.py) exactly as before — never a pydantic discriminated union,
# for the same reason codex's module gives: an unknown discriminant value must
# stay the `ignored` verdict it already gets, and only a RECOGNISED one is
# strict-validated against the model that owns it. A record whose top-level
# `type` this package never reads a field of (Claude Code writes ~15 of them —
# `last-prompt`, `permission-mode`, `mode`, `relocated`, `worktree-state`,
# `file-history-delta`, `file-history-snapshot`, `custom-title`, `atis-latch`,
# `pr-link`, `agent-setting`, `fork-context-ref`, `bridge-session`, and more to
# come) is never handed to a model at all — the same "ignored, not failed"
# outcome parse_line already gives it.
from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Annotated, Generic, Literal, TypeVar, Union

from pydantic import BaseModel, ConfigDict, Field, RootModel

from harness.impl.claude_code.ids import (
    ClaudeCodeActorId,
    ClaudeCodeCallId,
    ClaudeCodeMessageId,
    ClaudeCodeSessionId,
    ClaudeCodeShellId,
    ClaudeCodeTaskId,
    ClaudeCodeTaskListId,
    ClaudeCodeTurnId,
)
# The one config every FOREIGN payload model shares — see the module header.
FOREIGN = ConfigDict(extra="forbid", frozen=True)

# The escape hatch for a shape that is open by the VENDOR's contract, not by
# an oversight of ours — see the module header.
OPEN_FOREIGN = ConfigDict(extra="ignore", frozen=True)


class ForeignMetadata(BaseModel):
    """Named vendor metadata that this adapter deliberately does not interpret."""

    model_config = OPEN_FOREIGN


class PermissionRule(BaseModel):
    """One native rule in a permission update entry."""

    model_config = FOREIGN
    toolName: str
    ruleContent: str | None = None


class PermissionUpdate(BaseModel):
    """One permission change that Claude offers or a hook returns."""

    model_config = FOREIGN
    type: str
    rules: list[PermissionRule] | None = None
    behavior: str | None = None
    destination: str | None = None
    mode: str | None = None


class ImageSource(BaseModel):
    model_config = OPEN_FOREIGN
    type: str | None = None
    media_type: str | None = None
    data: str | None = None


class TranscriptRecordHeader(BaseModel):
    """Only the discriminator, used before the recognized record's strict model."""

    model_config = OPEN_FOREIGN
    type: str | None = None


class TranscriptDocument(BaseModel):
    """Common fields read independently of a transcript record's subtype."""

    model_config = OPEN_FOREIGN
    type: str | None = None
    uuid: str | None = None
    parentUuid: str | None = None
    timestamp: str | int | float | None = None
    cwd: str | None = None
    transcript_path: str | None = None
    message: MessageObject | None = None
    agentName: str | None = None
    aiTitle: str | None = None
    summary: str | None = None


class PreservedCompactSegment(BaseModel):
    model_config = FOREIGN
    headUuid: str
    anchorUuid: str
    tailUuid: str


class PreservedCompactMessages(BaseModel):
    model_config = FOREIGN
    anchorUuid: str
    uuids: tuple[str, ...]
    allUuids: tuple[str, ...]


class CompactMetadata(BaseModel):
    model_config = FOREIGN
    preTokens: int | None = None
    trigger: str | None = None
    postTokens: int | None = None
    cumulativeDroppedTokens: int | None = None
    durationMs: int | float | None = None
    preCompactDiscoveredTools: tuple[str, ...] | None = None
    preservedSegment: PreservedCompactSegment | None = None
    preservedMessages: PreservedCompactMessages | None = None


class HookSummaryInfo(BaseModel):
    """One command/prompt hook measured in a `stop_hook_summary` record."""

    model_config = FOREIGN
    command: str
    durationMs: int | float | None = None
    promptText: str | None = None


# === The transcript record register (transcript.py) ==========================
# Claude Code's `~/.claude/projects/**/*.jsonl` line grammar. Every field below
# is corpus-observed (a 549k-line scan of this machine's own transcripts,
# 2026-08-22) for the record `type` it sits under; parse_line() dispatches on
# `type` BEFORE any model sees the record, so the ~15 other top-level types
# this package never reads a field of never reach one of these.


class TextBlock(BaseModel):
    model_config = FOREIGN
    type: Literal["text"] = "text"
    text: str | None = None


class DirectCaller(BaseModel):
    """Claude Code's typed marker for a tool call made by the lead agent."""

    model_config = FOREIGN
    type: Literal["direct"] = "direct"


class ToolUseBlock(BaseModel):
    model_config = FOREIGN
    type: Literal["tool_use"] = "tool_use"
    id: str | None = None
    name: str | None = None
    caller: DirectCaller | None = None
    # The tool's own arguments — a genuinely open, per-tool shape (module
    # header); read generically here and validated against the specific
    # tool's ARGUMENTS model only once TOOL_KINDS has named it (toolcalls.py).
    input: ToolArguments | None = None


class InnerContentBlock(BaseModel):
    """One block of a tool_result's OWN `content` — GENUINELY open (module
    header): it is whatever the tool that answered chose to put there, a
    Claude Code built-in's plain text/image or an MCP tool's own shape.
    Declared as far as reality allows: `text`/`tool_name`/`source` are the
    three fields transcript.result_text() reads (the corpus's `text`,
    `tool_reference`, `image` block kinds); anything else rides along unread.
    """

    model_config = OPEN_FOREIGN
    type: str | None = None
    text: str | None = None
    tool_name: str | None = None
    source: ImageSource | None = None


class ToolResultBlock(BaseModel):
    model_config = FOREIGN
    type: Literal["tool_result"] = "tool_result"
    tool_use_id: ClaudeCodeCallId | None = None
    is_error: bool | None = None
    content: str | list[InnerContentBlock | str] | None = None


class ThinkingBlock(BaseModel):
    model_config = FOREIGN
    type: Literal["thinking"] = "thinking"
    thinking: str | None = None
    signature: str | None = None


class ImageBlock(BaseModel):
    model_config = FOREIGN
    type: Literal["image"] = "image"
    source: ImageSource | None = None


class FallbackBlock(BaseModel):
    """A model-swap notice Claude Code injects into `message.content` itself
    (corpus: `{"type": "fallback", "from": "...", "to": "..."}`) — nothing
    here reads it, declared so its shape does not silently drift unnoticed.
    `from` is aliased: it is a Python keyword."""

    model_config = FOREIGN
    type: Literal["fallback"] = "fallback"
    from_: str | None = Field(default=None, alias="from")
    to: str | None = None


MessageContentBlock = Annotated[
    Union[TextBlock, ToolUseBlock, ToolResultBlock, ThinkingBlock, ImageBlock, FallbackBlock],
    Field(discriminator="type"),
]


class UsageOutputTokensDetails(BaseModel):
    model_config = FOREIGN
    thinking_tokens: int | float


class UsageServerToolUse(BaseModel):
    model_config = FOREIGN
    web_search_requests: int | float
    web_fetch_requests: int | float


class UsageCacheCreation(BaseModel):
    model_config = FOREIGN
    ephemeral_1h_input_tokens: int | float
    ephemeral_5m_input_tokens: int | float


class UsageIterationType(StrEnum):
    MESSAGE = "message"
    FALLBACK_MESSAGE = "fallback_message"


class UsageIteration(BaseModel):
    model_config = FOREIGN
    input_tokens: int | float
    output_tokens: int | float
    cache_read_input_tokens: int | float
    cache_creation_input_tokens: int | float
    cache_creation: UsageCacheCreation
    type: UsageIterationType
    model: str | None = None


class UsageServiceTier(StrEnum):
    STANDARD = "standard"


class UsageSpeed(StrEnum):
    STANDARD = "standard"


class UsageInferenceGeo(StrEnum):
    NOT_AVAILABLE = "not_available"


class MessageUsage(BaseModel):
    model_config = FOREIGN
    input_tokens: int | float | None = None
    cache_creation_input_tokens: int | float | None = None
    cache_read_input_tokens: int | float | None = None
    output_tokens: int | float | None = None
    output_tokens_details: UsageOutputTokensDetails | None = None
    server_tool_use: UsageServerToolUse | None = None
    service_tier: UsageServiceTier | None = None
    cache_creation: UsageCacheCreation | None = None
    inference_geo: UsageInferenceGeo | None = None
    iterations: tuple[UsageIteration, ...] | None = None
    speed: UsageSpeed | None = None


class MessageObject(BaseModel):
    """The `message` object a `user`/`assistant` transcript record carries —
    one shape shared by both (corpus: the assistant's `usage`/`model`/
    `stop_reason` sit beside the same `id`/`role`/`content` a user message
    carries, just usually empty on the user side)."""

    model_config = FOREIGN
    id: str | None = None
    type: str | None = None
    role: str | None = None
    model: str | None = None
    content: str | list[MessageContentBlock] | None = None
    stop_reason: str | None = None
    stop_sequence: str | None = None
    stop_details: ForeignMetadata | None = None
    usage: MessageUsage | None = None
    container: ForeignMetadata | None = None
    context_management: ForeignMetadata | None = None
    diagnostics: ForeignMetadata | None = None


class Origin(BaseModel):
    """A `user` record's `origin` — read for `origin.kind == "task-notification"`
    (transcript.parse_line); the other fields ride along unread but are
    corpus-observed on the same object."""

    model_config = FOREIGN
    kind: str | None = None
    name: str | None = None
    senderTaskId: str | None = None
    body: str | None = None
    from_: str | None = Field(default=None, alias="from")


class TeammateIdleNotificationDocument(BaseModel):
    """The JSON body in one Claude team ``idle_notification`` message."""

    model_config = FOREIGN
    type: Literal["idle_notification"] = "idle_notification"
    from_: str = Field(alias="from")
    timestamp: str | None = None
    idleReason: str
    failureReason: str | None = None


class TeammateMessageBodyHeader(BaseModel):
    """Only the discriminator for an optional JSON teammate message body."""

    model_config = OPEN_FOREIGN
    type: str | None = None


class UserRecord(BaseModel):
    model_config = FOREIGN
    type: Literal["user"] = "user"
    message: MessageObject | None = None
    origin: Origin | None = None
    # The tool result sidecar — GENUINELY open (module header): its shape
    # varies by WHICH tool answered, from a plain string to any of the
    # dozens of per-tool result documents toolUseResult.py's corpus scan
    # turned up. Read generically here; the specific tool's RESPONSE model
    # (ToolResponse below) validates it once the call it answers is known.
    toolUseResult: ToolResponse | ToolResponseBlocks | str | None = None
    uuid: str | None = None
    parentUuid: str | None = None
    sessionId: str | None = None
    session_id: ClaudeCodeSessionId | None = None
    timestamp: str | None = None
    cwd: str | None = None
    gitBranch: str | None = None
    entrypoint: str | None = None
    slug: str | None = None
    userType: str | None = None
    version: str | None = None
    agentId: str | None = None
    isSidechain: bool | None = None
    isMeta: bool | None = None
    isCompactSummary: bool | None = None
    isVisibleInTranscriptOnly: bool | None = None
    interruptedMessageId: str | None = None
    permissionMode: str | None = None
    promptId: str | None = None
    promptSource: str | None = None
    sourceToolAssistantUUID: str | None = None
    sourceToolUseID: str | None = None
    toolDenialKind: str | None = None
    turnCompanion: bool | None = None
    queueSkipAttachments: bool | None = None
    userFeedback: ForeignMetadata | str | None = None
    imagePasteIds: list[str | int] | None = None


class AssistantRecord(BaseModel):
    model_config = FOREIGN
    type: Literal["assistant"] = "assistant"
    message: MessageObject | None = None
    uuid: str | None = None
    parentUuid: str | None = None
    sessionId: str | None = None
    session_id: ClaudeCodeSessionId | None = None
    timestamp: str | None = None
    cwd: str | None = None
    gitBranch: str | None = None
    entrypoint: str | None = None
    slug: str | None = None
    userType: str | None = None
    version: str | None = None
    agentId: str | None = None
    isSidechain: bool | None = None
    isAbortedMidStream: bool | None = None
    isApiErrorMessage: bool | None = None
    apiErrorStatus: str | int | None = None
    error: str | None = None
    errorDetails: ForeignMetadata | None = None
    requestId: str | None = None
    effort: str | HookEffort | None = None
    attributionAgent: str | None = None
    attributionMcpServer: str | None = None
    attributionMcpTool: str | None = None
    attributionPlugin: str | None = None
    attributionSkill: str | None = None
    quotaLimits: ForeignMetadata | None = None


class SystemRecord(BaseModel):
    """A `type=system` record, of any `subtype` — one model shared by every
    subtype (corpus: `stop_hook_summary`, `turn_duration`, `away_summary`,
    `local_command`, `compact_boundary`, `informational`,
    `model_consent_fallback`, `model_refusal_fallback`, `bridge_status`), each
    of which uses a subset of the union of fields below. parse_line() reads
    `subtype` first and only two of these carry content this package acts on
    (`compact_boundary`'s `compactMetadata`, `away_summary`/plain `content`)."""

    model_config = FOREIGN
    type: Literal["system"] = "system"
    subtype: str | None = None
    content: str | None = None
    compactMetadata: CompactMetadata | None = None
    uuid: str | None = None
    parentUuid: str | None = None
    logicalParentUuid: str | None = None
    sessionId: str | None = None
    session_id: ClaudeCodeSessionId | None = None
    timestamp: str | None = None
    cwd: str | None = None
    gitBranch: str | None = None
    entrypoint: str | None = None
    slug: str | None = None
    userType: str | None = None
    version: str | None = None
    agentId: str | None = None
    isSidechain: bool | None = None
    isMeta: bool | None = None
    level: str | None = None
    toolUseID: str | None = None
    toolUseId: str | None = None
    stopReason: str | None = None
    hasOutput: bool | None = None
    hookAdditionalContext: tuple[str, ...] | None = None
    hookCount: int | None = None
    hookErrors: tuple[str, ...] | None = None
    hookInfos: tuple[HookSummaryInfo, ...] | None = None
    preventContinuation: bool | None = None
    preventedContinuation: bool | None = None
    durationMs: int | float | None = None
    messageCount: int | None = None
    pendingBackgroundAgentCount: int | None = None
    choice: str | None = None
    fallbackModel: str | None = None
    originalModel: str | None = None
    persistedAsDefault: bool | None = None
    apiRefusalCategory: str | None = None
    apiRefusalExplanation: str | None = None
    direction: str | None = None
    refusedUserMessageUuid: str | None = None
    requestId: str | None = None
    trigger: str | None = None
    url: str | None = None


class GoalStatusAttachment(BaseModel):
    """An `attachment.type == "goal_status"` body — the one attachment kind
    parse_line reads a field of (`condition`/`met`/`reason`)."""

    model_config = FOREIGN
    type: Literal["goal_status"] = "goal_status"
    condition: str | None = None
    met: bool | None = None
    reason: str | None = None
    durationMs: int | float | None = None
    iterations: int | None = None
    sentinel: str | None = None
    tokens: int | None = None


class QueuedCommandAttachment(BaseModel):
    """An `attachment.type == "queued_command"` body — the other attachment
    kind parse_line reads a field of (`commandMode`/`prompt`)."""

    model_config = FOREIGN
    type: Literal["queued_command"] = "queued_command"
    commandMode: str | None = None
    prompt: str | None = None
    isMeta: bool | None = None
    origin: Origin | None = None
    source_uuid: str | None = None
    timestamp: str | None = None


class AttachmentHeader(BaseModel):
    model_config = OPEN_FOREIGN
    type: str | None = None


AttachmentBody = TypeVar("AttachmentBody", bound=BaseModel)


class AttachmentRecord(BaseModel, Generic[AttachmentBody]):
    """A `type=attachment` record. Claude Code writes ~28 `attachment.type`
    values (corpus: `hook_success`, `total_tokens_reminder`, `skill_listing`,
    …); only `goal_status`/`queued_command` are read a field of, so the
    `attachment` body itself stays a JSON object here — `_attachment_body`
    (transcript.py) is what dispatches THOSE two into their own strict model,
    the same two-step "peek the discriminant, then validate" every other
    register in this module uses."""

    model_config = FOREIGN
    type: Literal["attachment"] = "attachment"
    attachment: AttachmentBody | None = None
    uuid: str | None = None
    parentUuid: str | None = None
    sessionId: str | None = None
    session_id: ClaudeCodeSessionId | None = None
    timestamp: str | None = None
    cwd: str | None = None
    gitBranch: str | None = None
    entrypoint: str | None = None
    slug: str | None = None
    userType: str | None = None
    version: str | None = None
    agentId: str | None = None
    isSidechain: bool | None = None


class QueueOperationRecord(BaseModel):
    """A `type=queue-operation` record — the enqueue half of a
    task-notification's delivery. Most notifications also have a `user` copy.
    A child background command or a resumed agent's later completion can have
    only this copy."""

    model_config = FOREIGN
    type: Literal["queue-operation"] = "queue-operation"
    operation: str | None = None
    # The same <task-notification> XML string a `user` record's plain-string
    # content carries (transcript.py header) — a raw string here, not JSON.
    content: str | ForeignMetadata | None = None
    sessionId: str | None = None
    timestamp: str | None = None
    # Claude Code adds this to `remove` records (currently
    # `absorbed_mid_turn`) when a queued prompt is consumed during a turn.
    reason: str | None = None


# `document["type"]` dispatches to one of UserRecord/AssistantRecord/
# SystemRecord/AttachmentRecord/QueueOperationRecord above — a plain
# `if t == "user":` chain in transcript.py's parse_line, for the same "smart
# union" reason the module header gives, so no dispatch DICT is declared here.
# Every OTHER top-level type Claude Code writes (`last-prompt`,
# `permission-mode`, `mode`, `ai-title`, `relocated`, `worktree-state`,
# `file-history-delta`, `file-history-snapshot`, `agent-name`, `summary`,
# `custom-title`, `atis-latch`, `pr-link`, `agent-setting`, `fork-context-ref`,
# `bridge-session`) is read only for the three-field TITLE shape below (an
# `agent-name`/`ai-title`/`summary` record) or not at all — see TitleRecord.


class TitleRecord(BaseModel):
    """An `agent-name` / `ai-title` / `summary` record — the three shapes
    transcript_metadata (messages.py) reads a naming fact out of. One model:
    each carries exactly one of the three text fields plus the two identity
    fields every transcript record type shares."""

    model_config = FOREIGN
    type: Literal["agent-name", "ai-title", "summary"]
    agentName: str | None = None
    aiTitle: str | None = None
    summary: str | None = None
    sessionId: str | None = None
    uuid: str | None = None


# === The tool call bodies (toolcalls.py) ======================================
# What TOOL_KINDS names a call — Bash, Read, AskUserQuestion, and so on — is a
# CLOSED, small vocabulary (toolcalls.TOOL_KINDS); an unlisted native tool name
# raises UnknownRawEvent there, which is this register's own "unknown kind
# stays ignored" outcome, decided before ANY of the models below is reached.
# Each declared ARGUMENTS/RESPONSE model below is scoped to the KIND its tool
# belongs to, not to one native tool name, because Claude Code's own built-in
# tools of one kind share a body (Read/Write/Edit all carry `file_path`).
#
# OPEN_FOREIGN throughout this section: a tool's arguments and its response are
# the vendor's own per-tool contract (an MCP server's schema above all), not
# ours, and several of the native names TOOL_KINDS lists (`MultiEdit`,
# `NotebookEdit`, `Grep`, `Glob`, `mcp__node_repl__js`, `exec_command`,
# `read_command`, `py`, `GenerateImage`) have no fixture in this machine's own
# corpus to declare exhaustively (module header) — the fields below are what
# toolcalls.py itself reads, plus what a corpus sample (this machine's
# `raw_events` table and its own `~/.claude/projects` transcripts, 2026-08-22)
# showed for the better-travelled tools of each kind.


class ShellArguments(BaseModel):
    """Bash / Monitor / exec_command / read_command / py / the node REPL MCP
    tool — everything TOOL_KINDS maps to `"shell"`. `command` is read by
    every one of them; `run_in_background` only by Bash (_shell_started)."""

    model_config = OPEN_FOREIGN
    command: str | list[str] | None = None
    description: str | None = None
    run_in_background: bool | None = None
    timeout: int | float | None = None


class QuestionOption(BaseModel):
    model_config = OPEN_FOREIGN
    label: str | None = None
    description: str | None = None


class Question(BaseModel):
    model_config = OPEN_FOREIGN
    id: str | int | None = None
    header: str | None = None
    question: str | None = None
    multiSelect: bool | None = None
    options: list[QuestionOption] | None = None


class QuestionAnswers(RootModel[Mapping[str, str | list[str]]]):
    pass


class ToolArguments(BaseModel):
    """Declared superset of the fields read from every supported tool input."""

    model_config = OPEN_FOREIGN
    command: str | list[str] | None = None
    description: str | None = None
    run_in_background: bool | None = None
    timeout: int | float | None = None
    task_id: ClaudeCodeShellId | None = None
    file_path: str | None = None
    notebook_path: str | None = None
    content: str | None = None
    old_string: str | None = None
    new_string: str | None = None
    replace_all: bool | None = None
    limit: int | None = None
    offset: int | None = None
    pattern: str | None = None
    query: str | None = None
    max_results: int | None = None
    allowed_domains: list[str] | None = None
    url: str | None = None
    prompt: str | None = None
    name: str | None = None
    path: str | None = None
    branch: str | None = None
    action: str | None = None
    discard_changes: bool | None = None
    skill: str | None = None
    args: str | None = None
    subagent_type: str | None = None
    model: str | None = None
    team_name: str | None = None
    isolation: str | None = None
    recipient: str | None = None
    to: str | None = None
    message: str | None = None
    summary: str | None = None
    questions: list[Question] | None = None
    answers: QuestionAnswers | None = None
    annotations: ForeignMetadata | None = None
    plan: str | None = None
    planFilePath: str | None = None


# `tool_kind(native_name)` names which of the ARGUMENTS models above owns a
# call's input; toolcalls.py's own per-kind methods (_shell_started,
# file_facts, …) each call the one that is theirs directly, so no dispatch
# DICT is declared here — `"ignored"` has none: nothing ever reads a field of
# one, so no model was worth declaring for it either.


class PatchHunk(BaseModel):
    """One `structuredPatch` hunk of a file-edit tool's response — closed and
    ours to expect exactly (toolcalls.structured_patch reads every field)."""

    model_config = FOREIGN
    oldStart: int | None = None
    oldLines: int | None = None
    newStart: int | None = None
    newLines: int | None = None
    lines: list[str] | None = None


class ToolResponseBlocks(RootModel[list[InnerContentBlock | str]]):
    pass


class ToolResponseImageDimensions(BaseModel):
    """Dimensions Claude records when Read returns an image."""

    model_config = FOREIGN
    originalWidth: int | None = None
    originalHeight: int | None = None
    displayWidth: int | None = None
    displayHeight: int | None = None


class ToolResponseFile(BaseModel):
    """The built-in Read tool's text or image result."""

    model_config = FOREIGN
    filePath: str | None = None
    content: str | None = None
    numLines: int | None = None
    startLine: int | None = None
    totalLines: int | None = None
    truncatedByTokenCap: bool | None = None
    base64: str | None = None
    type: str | None = None
    originalSize: int | None = None
    dimensions: ToolResponseImageDimensions | None = None


class WebSearchLink(BaseModel):
    """One readable link in Claude WebSearch's open tool response."""

    model_config = OPEN_FOREIGN
    title: str | None = None
    url: str | None = None


class WebSearchResultSet(BaseModel):
    """The link-bearing member of WebSearch's mixed result list."""

    model_config = OPEN_FOREIGN
    content: list[WebSearchLink] | None = None


class ToolResponse(BaseModel):
    """A tool call's answer — the hook path's `tool_response`, the
    transcript's `toolUseResult` sidecar, and tool_result()'s synthetic
    `{"tool_use_id": …, "tool_response": …}` all converge on this one shape
    (support.py/toolcalls.py already read it as one interchangeable thing).
    GENUINELY open (module header): its key set is chosen by whichever tool
    answered. Declared as far as reality allows — every field
    structured_patch/_shell_finished/_assignment_finished/plan_resolution
    reads, plus `content`/`type` for the image-edit variants a corpus scan of
    this machine's own transcripts turned up (`create`/`update`/`text`/
    `image`, each carrying `content`/`filePath`/`structuredPatch`)."""

    model_config = OPEN_FOREIGN
    content: str | ToolResponseBlocks | None = None
    result: str | ToolResponseBlocks | None = None
    file: ToolResponseFile | None = None
    type: str | None = None
    structuredPatch: list[PatchHunk] | None = None
    backgroundTaskId: str | None = None
    backgroundedByUser: bool | None = None
    isAsync: bool | None = None
    status: str | None = None
    name: str | None = None
    agentId: str | None = None
    agent_id: ClaudeCodeActorId | None = None
    teammate_id: ClaudeCodeActorId | None = None
    team_name: str | None = None
    taskId: str | None = None
    planWasEdited: bool | None = None
    matches: list[str] | None = None
    filenames: list[str] | None = None
    query: str | None = None
    results: list[WebSearchResultSet | str] | None = None


class ToolCallNative(BaseModel):
    """The "one call, however it arrived" shape tool_started/tool_finished
    read: a hook's PreToolUse/PostToolUse delivery (HookPayload, below) OR a
    transcript assistant block's tool_use OR tool_result's own synthetic
    stand-in. All three name the call the same two ways (`tool_use_id`/`id`,
    `tool_name`/`name`) and carry the same two payload fields
    (`tool_input`/`input`, `tool_response`) under different names depending on
    which of the two raw event streams it rode."""

    model_config = OPEN_FOREIGN
    tool_use_id: ClaudeCodeCallId | None = None
    id: str | None = None
    tool_name: str | None = None
    name: str | None = None
    tool_input: ToolArguments | None = None
    input: ToolArguments | None = None
    tool_response: ToolResponse | ToolResponseBlocks | str | None = None


# === The hook delivery (hooks.py, hooks/foreground.py, hooks/gateway.py) =====


class HookEffort(BaseModel):
    model_config = FOREIGN
    level: str | None = None


class HookPayload(BaseModel):
    """One hook delivery's JSON body — Claude Code's own hook contract, closed
    and version-stable (unlike a tool's own arguments), so FOREIGN. Every
    field below is corpus-observed: this machine's own `raw_events` table
    (`harness='claude_code' and source_type='hook'`), grouped by
    `hook_event_name`, across all 20 hook events this installation has fired
    (2026-08-23) — `PreToolUse`, `PostToolUse`, `PostToolBatch`, `Stop`,
    `SubagentStart`, `SubagentStop`, `SessionStart`, `SessionEnd`,
    `PreCompact`, `PostCompact`, `Notification`, `MessageDisplay`,
    `UserPromptSubmit`, `InstructionsLoaded`, `ConfigChange`, `TeammateIdle`,
    `PostToolUseFailure`, `PermissionRequest`, `TaskCreated`, `TaskCompleted`.
    One model for all of them, on
    the same footing as SystemRecord above: each event uses a subset of the
    union below, and `hook_event_name` is read first (translate_hook) to pick
    the branch, so a field one event never carries simply stays None on it."""

    model_config = FOREIGN
    hook_event_name: str | None = None
    hook_event_id: str | None = None
    uuid: str | None = None
    session_id: ClaudeCodeSessionId | None = None
    session_title: str | None = None
    transcript_path: str | None = None
    agent_transcript_path: str | None = None
    cwd: str | None = None
    prompt_id: str | None = None
    permission_mode: str | None = None
    effort: str | HookEffort | None = None
    agent_id: ClaudeCodeActorId | None = None
    agent_type: str | None = None
    tool_use_id: ClaudeCodeCallId | None = None
    tool_name: str | None = None
    tool_input: ToolArguments | None = None
    tool_response: ToolResponse | ToolResponseBlocks | str | None = None
    tool_calls: list[ToolCallNative] | None = None
    duration_ms: int | float | None = None
    error: str | None = None
    is_interrupt: bool | None = None
    reason: str | None = None
    stop_hook_active: bool | None = None
    last_assistant_message: str | None = None
    seconds_since_last_response: int | float | None = None
    context_tokens: int | None = None
    prompt_cache_likely_expired: bool | None = None
    estimated_cache_write_usd: int | float | None = None
    background_tasks: list[ForeignMetadata] | None = None
    session_crons: list[ForeignMetadata] | None = None
    message: str | None = None
    message_id: ClaudeCodeMessageId | None = None
    delta: str | None = None
    final: bool | None = None
    index: int | None = None
    turn_id: ClaudeCodeTurnId | None = None
    notification_type: str | None = None
    permission_suggestions: list[PermissionUpdate] | None = None
    prompt: str | None = None
    custom_instructions: str | None = None
    compact_summary: str | None = None
    trigger: str | None = None
    model: str | None = None
    source: str | None = None
    file_path: str | None = None
    load_reason: str | None = None
    memory_type: str | None = None
    team_name: str | None = None
    teammate_name: str | None = None
    task_id: ClaudeCodeTaskId | None = None
    task_subject: str | None = None
    task_description: str | None = None

    def shell_input(self) -> ShellArguments:
        return (
            ShellArguments()
            if self.tool_input is None
            else ShellArguments.model_validate_json(self.tool_input.model_dump_json())
        )


# === The OTLP metrics document (otel.py, otel/gateway.py) ====================


class OTelAttributeValue(BaseModel):
    model_config = OPEN_FOREIGN
    stringValue: str | None = None
    intValue: str | int | None = None
    doubleValue: int | float | None = None

    def scalar(self) -> str | int | float | None:
        return self.stringValue or self.intValue or self.doubleValue


class OTelAttribute(BaseModel):
    model_config = OPEN_FOREIGN
    key: str = ""
    value: OTelAttributeValue = Field(default_factory=OTelAttributeValue)


class OTelDataPoint(BaseModel):
    model_config = OPEN_FOREIGN
    attributes: list[OTelAttribute] = Field(default_factory=list)
    asDouble: int | float | None = None
    asInt: str | int | None = None

    def attribute(self, key: str) -> str | int | float | None:
        return next(
            (attribute.value.scalar() for attribute in self.attributes if attribute.key == key),
            None,
        )


class OTelSum(BaseModel):
    model_config = OPEN_FOREIGN
    dataPoints: list[OTelDataPoint] = Field(default_factory=list)


class OTelMetric(BaseModel):
    model_config = OPEN_FOREIGN
    name: str = ""
    sum: OTelSum | None = None


class OTelScopeMetrics(BaseModel):
    model_config = OPEN_FOREIGN
    metrics: list[OTelMetric] = Field(default_factory=list)


class OTelResourceMetrics(BaseModel):
    model_config = OPEN_FOREIGN
    scopeMetrics: list[OTelScopeMetrics] = Field(default_factory=list)


class OTelMetricsDocument(BaseModel):
    model_config = OPEN_FOREIGN
    resourceMetrics: list[OTelResourceMetrics] = Field(default_factory=list)


# === The launch selection (messages.py launch_selections) ====================


class LaunchSelectionDocument(BaseModel):
    """The launch observation the hook gateway records from the CLI's
    inherited environment (`--model`/`--effort`) — closed, ours to define on
    both ends (hooks/gateway.py writes it, launch_selections reads it)."""

    model_config = FOREIGN
    model: str | None = None
    effort: str | None = None


# === The agent meta.json sidecar (model.py agent_meta, canonical/messages.py
# session_events) =============================================================


class AgentMetaFile(BaseModel):
    """A subagent's `agent-<id>.meta.json` sidecar — corpus-observed (this
    machine's own sidecars, 2026-08-22): every field any of them has ever
    carried, though `description`/`taskKind` are the only two read."""

    model_config = FOREIGN
    agentType: str | None = None
    color: str | None = None
    customAgentType: str | None = None
    description: str | None = None
    isFork: bool | None = None
    model: str | None = None
    name: str | None = None
    parentAgentId: str | None = None
    permissionMode: str | None = None
    planModeRequired: bool | None = None
    spawnDepth: int | None = None
    stoppedByUser: bool | None = None
    taskKind: str | None = None
    teamName: str | None = None
    toolUseId: str | None = None
    worktreeBranch: str | None = None
    worktreeCleanlyRemoved: bool | None = None
    worktreePath: str | None = None


# === The session task file (canonical/sources.py ClaudeTaskRawEventSource) ===


class TaskFile(BaseModel):
    """One `~/.claude/tasks/session-<id>/<task-id>.json` snapshot — corpus-
    observed (this machine's own task files, 2026-08-22): every session task
    Claude Code has ever written carries exactly these eight fields."""

    model_config = FOREIGN
    id: str | int | None = None
    subject: str | None = None
    description: str | None = None
    activeForm: str | None = None
    status: str | None = None
    owner: str | None = None
    blocks: list[str | int] | None = None
    blockedBy: list[str | int] | None = None


# === The task list membership document (translator.py) =======================


class TaskListDocument(BaseModel):
    """The `task_list` raw event's payload — OURS on both ends
    (ClaudeTaskRawEventSource writes it, ClaudeCanonicalTranslator reads it)."""

    model_config = FOREIGN
    list_id: ClaudeCodeTaskListId | None = None
    task_ids: list[str] | None = None


class TaskSnapshot(RootModel[tuple[TaskFile, ...]]):
    pass
