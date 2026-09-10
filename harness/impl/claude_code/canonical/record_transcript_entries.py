# Copyright (c) 2026 Zhambyl Yermagambet
"""Record transcript entries."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from harness.impl.claude_code.canonical.record_common import FOREIGN, OPEN_FOREIGN, ForeignMetadata
from harness.impl.claude_code.canonical.record_tool_response import HookEffort, ToolResponse
from harness.impl.claude_code.canonical.record_tool_response_base import ToolResponseBlocks
from harness.impl.claude_code.canonical.record_transcript_common import CompactMetadata, HookSummaryInfo, Origin
from harness.impl.claude_code.canonical.record_usage import MessageObject
from harness.impl.claude_code.ids import (
    ClaudeCodeSessionId,
)


class TeammateIdleNotificationDocument(BaseModel):
    """The JSON body in one Claude team ``idle_notification`` message."""

    model_config = FOREIGN
    type: Literal["idle_notification"] = "idle_notification"
    from_: str = Field(alias="from")
    timestamp: str | None = None
    idle_reason: Annotated[str, Field(alias="idleReason")]
    failure_reason: Annotated[str | None, Field(alias="failureReason")] = None


class TeammateMessageBodyHeader(BaseModel):
    """Only the discriminator for an optional JSON teammate message body."""

    model_config = OPEN_FOREIGN
    type: str | None = None


class UserRecord(BaseModel):
    """Represent user record."""

    model_config = FOREIGN
    type: Literal["user"] = "user"
    message: MessageObject | None = None
    origin: Origin | None = None
    # The tool result sidecar — GENUINELY open (module header): its shape
    # varies by WHICH tool answered, from a plain string to any of the
    # dozens of per-tool result documents toolUseResult.py's corpus scan
    # turned up. Read generically here; the specific tool's RESPONSE model
    # (ToolResponse below) validates it once the call it answers is known.
    tool_use_result: Annotated[ToolResponse | ToolResponseBlocks | str | None, Field(alias="toolUseResult")] = None
    uuid: str | None = None
    parent_uuid: Annotated[str | None, Field(alias="parentUuid")] = None
    external_session_id: Annotated[str | None, Field(alias="sessionId")] = None
    session_id: ClaudeCodeSessionId | None = None
    timestamp: str | None = None
    cwd: str | None = None
    git_branch: Annotated[str | None, Field(alias="gitBranch")] = None
    entrypoint: str | None = None
    slug: str | None = None
    user_type: Annotated[str | None, Field(alias="userType")] = None
    version: str | None = None
    agent_id: Annotated[str | None, Field(alias="agentId")] = None
    is_sidechain: Annotated[bool | None, Field(alias="isSidechain")] = None
    is_meta: Annotated[bool | None, Field(alias="isMeta")] = None
    is_compact_summary: Annotated[bool | None, Field(alias="isCompactSummary")] = None
    is_visible_in_transcript_only: Annotated[bool | None, Field(alias="isVisibleInTranscriptOnly")] = None
    interrupted_message_id: Annotated[str | None, Field(alias="interruptedMessageId")] = None
    permission_mode: Annotated[str | None, Field(alias="permissionMode")] = None
    prompt_id: Annotated[str | None, Field(alias="promptId")] = None
    prompt_source: Annotated[str | None, Field(alias="promptSource")] = None
    source_tool_assistant_uuid: Annotated[str | None, Field(alias="sourceToolAssistantUUID")] = None
    source_tool_use_id: Annotated[str | None, Field(alias="sourceToolUseID")] = None
    tool_denial_kind: Annotated[str | None, Field(alias="toolDenialKind")] = None
    turn_companion: Annotated[bool | None, Field(alias="turnCompanion")] = None
    queue_skip_attachments: Annotated[bool | None, Field(alias="queueSkipAttachments")] = None
    user_feedback: Annotated[ForeignMetadata | str | None, Field(alias="userFeedback")] = None
    image_paste_ids: Annotated[list[str | int] | None, Field(alias="imagePasteIds")] = None
    mcp_meta: Annotated[ForeignMetadata | None, Field(alias="mcpMeta")] = None


class AssistantRecord(BaseModel):
    """Represent assistant record."""

    model_config = FOREIGN
    type: Literal["assistant"] = "assistant"
    message: MessageObject | None = None
    uuid: str | None = None
    parent_uuid: Annotated[str | None, Field(alias="parentUuid")] = None
    external_session_id: Annotated[str | None, Field(alias="sessionId")] = None
    session_id: ClaudeCodeSessionId | None = None
    timestamp: str | None = None
    cwd: str | None = None
    git_branch: Annotated[str | None, Field(alias="gitBranch")] = None
    entrypoint: str | None = None
    slug: str | None = None
    user_type: Annotated[str | None, Field(alias="userType")] = None
    version: str | None = None
    agent_id: Annotated[str | None, Field(alias="agentId")] = None
    is_sidechain: Annotated[bool | None, Field(alias="isSidechain")] = None
    is_aborted_mid_stream: Annotated[bool | None, Field(alias="isAbortedMidStream")] = None
    is_api_error_message: Annotated[bool | None, Field(alias="isApiErrorMessage")] = None
    api_error_status: Annotated[str | int | None, Field(alias="apiErrorStatus")] = None
    error: str | None = None
    error_details: Annotated[ForeignMetadata | str | None, Field(alias="errorDetails")] = None
    request_id: Annotated[str | None, Field(alias="requestId")] = None
    api_block_index: Annotated[int | None, Field(alias="apiBlockIndex")] = None
    effort: str | HookEffort | None = None
    per_turn_effort: Annotated[str | HookEffort | None, Field(alias="perTurnEffort")] = None
    native_tool_inputs: Annotated[ForeignMetadata | None, Field(alias="wireToolInputs")] = None
    native_ingest_context: Annotated[ForeignMetadata | None, Field(alias="wireIngestContext")] = None
    attribution_agent: Annotated[str | None, Field(alias="attributionAgent")] = None
    attribution_mcp_server: Annotated[str | None, Field(alias="attributionMcpServer")] = None
    attribution_mcp_tool: Annotated[str | None, Field(alias="attributionMcpTool")] = None
    attribution_plugin: Annotated[str | None, Field(alias="attributionPlugin")] = None
    attribution_skill: Annotated[str | None, Field(alias="attributionSkill")] = None
    quota_limits: Annotated[ForeignMetadata | None, Field(alias="quotaLimits")] = None


class SystemRecord(BaseModel):
    """Represent system record.

    A `type=system` record, of any `subtype` — one model shared by every
        subtype (corpus: `stop_hook_summary`, `turn_duration`, `away_summary`,
        `local_command`, `compact_boundary`, `informational`,
        `model_consent_fallback`, `model_refusal_fallback`, `bridge_status`), each
        of which uses a subset of the union of fields below. parse_line() reads
        `subtype` first and only two of these carry content this package acts on
        (`compact_boundary`'s `compactMetadata`, `away_summary`/plain `content`).
    """

    model_config = FOREIGN
    type: Literal["system"] = "system"
    subtype: str | None = None
    content: str | None = None
    compact_metadata: Annotated[CompactMetadata | None, Field(alias="compactMetadata")] = None
    uuid: str | None = None
    parent_uuid: Annotated[str | None, Field(alias="parentUuid")] = None
    logical_parent_uuid: Annotated[str | None, Field(alias="logicalParentUuid")] = None
    external_session_id: Annotated[str | None, Field(alias="sessionId")] = None
    session_id: ClaudeCodeSessionId | None = None
    timestamp: str | None = None
    cwd: str | None = None
    git_branch: Annotated[str | None, Field(alias="gitBranch")] = None
    entrypoint: str | None = None
    slug: str | None = None
    user_type: Annotated[str | None, Field(alias="userType")] = None
    version: str | None = None
    agent_id: Annotated[str | None, Field(alias="agentId")] = None
    is_sidechain: Annotated[bool | None, Field(alias="isSidechain")] = None
    is_meta: Annotated[bool | None, Field(alias="isMeta")] = None
    level: str | None = None
    tool_use_uppercase_id: Annotated[str | None, Field(alias="toolUseID")] = None
    tool_use_id: Annotated[str | None, Field(alias="toolUseId")] = None
    stop_reason: Annotated[str | None, Field(alias="stopReason")] = None
    has_output: Annotated[bool | None, Field(alias="hasOutput")] = None
    hook_additional_context: Annotated[tuple[str, ...] | None, Field(alias="hookAdditionalContext")] = None
    hook_count: Annotated[int | None, Field(alias="hookCount")] = None
    hook_errors: Annotated[tuple[str, ...] | None, Field(alias="hookErrors")] = None
    hook_infos: Annotated[tuple[HookSummaryInfo, ...] | None, Field(alias="hookInfos")] = None
    prevent_continuation: Annotated[bool | None, Field(alias="preventContinuation")] = None
    prevented_continuation: Annotated[bool | None, Field(alias="preventedContinuation")] = None
    duration_ms: Annotated[int | float | None, Field(alias="durationMs")] = None
    message_count: Annotated[int | None, Field(alias="messageCount")] = None
    pending_background_agent_count: Annotated[int | None, Field(alias="pendingBackgroundAgentCount")] = None
    choice: str | None = None
    fallback_model: Annotated[str | None, Field(alias="fallbackModel")] = None
    original_model: Annotated[str | None, Field(alias="originalModel")] = None
    persisted_as_default: Annotated[bool | None, Field(alias="persistedAsDefault")] = None
    api_refusal_category: Annotated[str | None, Field(alias="apiRefusalCategory")] = None
    api_refusal_explanation: Annotated[str | None, Field(alias="apiRefusalExplanation")] = None
    direction: str | None = None
    refused_user_message_uuid: Annotated[str | None, Field(alias="refusedUserMessageUuid")] = None
    request_id: Annotated[str | None, Field(alias="requestId")] = None
    trigger: str | None = None
    url: str | None = None
