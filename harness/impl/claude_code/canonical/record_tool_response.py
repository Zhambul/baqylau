# Copyright (c) 2026 Zhambyl Yermagambet
"""Record tool response."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field

from harness.impl.claude_code.canonical.record_common import FOREIGN, OPEN_FOREIGN, ForeignMetadata, PermissionUpdate
from harness.impl.claude_code.canonical.record_questions import QuestionAnswers, ShellArguments, ToolArguments
from harness.impl.claude_code.canonical.record_tool_response_base import (
    PatchHunk,
    ToolResponseBlocks,
    ToolResponseFile,
    WebSearchResultSet,
)
from harness.impl.claude_code.ids import (
    ClaudeCodeActorId,
    ClaudeCodeCallId,
    ClaudeCodeMessageId,
    ClaudeCodeSessionId,
    ClaudeCodeTaskId,
    ClaudeCodeTurnId,
)


class ToolResponse(BaseModel):
    """Represent tool response.

    A tool call's answer — the hook path's `tool_response`, the
        transcript's `toolUseResult` sidecar, and tool_result()'s synthetic
        `{"tool_use_id": …, "tool_response": …}` all converge on this one shape
        (support.py/toolcalls.py already read it as one interchangeable thing).
        GENUINELY open (module header): its key set is chosen by whichever tool
        answered. Declared as far as reality allows — every field
        structured_patch/_shell_finished/_assignment_finished/plan_resolution
        reads, plus `content`/`type` for the image-edit variants a corpus scan of
        this machine's own transcripts turned up (`create`/`update`/`text`/
        `image`, each carrying `content`/`filePath`/`structuredPatch`).
    """

    model_config = OPEN_FOREIGN
    answers: QuestionAnswers | None = None
    content: str | ToolResponseBlocks | None = None
    result: str | ToolResponseBlocks | None = None
    file: ToolResponseFile | None = None
    type: str | None = None
    structured_patch: Annotated[list[PatchHunk] | None, Field(alias="structuredPatch")] = None
    background_task_id: Annotated[str | None, Field(alias="backgroundTaskId")] = None
    backgrounded_by_user: Annotated[bool | None, Field(alias="backgroundedByUser")] = None
    is_async: Annotated[bool | None, Field(alias="isAsync")] = None
    status: str | None = None
    name: str | None = None
    external_agent_id: Annotated[str | None, Field(alias="agentId")] = None
    agent_id: ClaudeCodeActorId | None = None
    teammate_id: ClaudeCodeActorId | None = None
    team_name: str | None = None
    task_id: Annotated[str | None, Field(alias="taskId")] = None
    plan_was_edited: Annotated[bool | None, Field(alias="planWasEdited")] = None
    matches: list[str] | None = None
    filenames: list[str] | None = None
    query: str | None = None
    search_results: list[WebSearchResultSet | str] | None = Field(default=None, alias="results")


class ToolCallNative(BaseModel):
    """Represent tool call native.

    The "one call, however it arrived" shape tool_started/tool_finished
        read: a hook's PreToolUse/PostToolUse delivery (HookPayload, below) OR a
        transcript assistant block's tool_use OR tool_result's own synthetic
        stand-in. All three name the call the same two ways (`tool_use_id`/`id`,
        `tool_name`/`name`) and carry the same two payload fields
        (`tool_input`/`input`, `tool_response`) under different names depending on
        which of the two raw event streams it rode.
    """

    model_config = OPEN_FOREIGN
    tool_use_id: ClaudeCodeCallId | None = None
    id: str | None = None
    tool_name: str | None = None
    name: str | None = None
    tool_input: ToolArguments | None = None
    input: ToolArguments | None = None
    tool_response: ToolResponse | ToolResponseBlocks | str | None = None


class HookEffort(BaseModel):
    """Represent hook effort."""

    model_config = FOREIGN
    level: str | None = None


class HookPayload(BaseModel):
    """Represent hook payload.

    One hook delivery's JSON body — Claude Code's own hook contract, closed
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
        the branch, so a field one event never carries simply stays None on it.
    """

    model_config = FOREIGN
    hook_event_name: str | None = None
    hook_event_id: str | None = None
    uuid: str | None = None
    session_id: ClaudeCodeSessionId | None = None
    session_title: str | None = None
    transcript_path: str | None = None
    agent_transcript_path: str | None = None
    cwd: str | None = None
    old_cwd: str | None = None
    new_cwd: str | None = None
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
    error_details: str | None = None
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
    parent_file_path: str | None = None
    load_reason: str | None = None
    memory_type: str | None = None
    team_name: str | None = None
    teammate_name: str | None = None
    task_id: ClaudeCodeTaskId | None = None
    task_subject: str | None = None
    task_description: str | None = None

    def shell_input(self) -> ShellArguments:
        """Return the shell input.

        Returns:
            Shell input.

        """
        return (
            ShellArguments()
            if self.tool_input is None
            else ShellArguments.model_validate_json(self.tool_input.model_dump_json())
        )
