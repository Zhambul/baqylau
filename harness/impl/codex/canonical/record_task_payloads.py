# Copyright (c) 2026 Zhambyl Yermagambet
"""Own task payloads models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from harness.impl.codex.canonical.record_config import FOREIGN, ForeignMetadata
from harness.impl.codex.ids_conversation_types import CodexTurnId
from harness.impl.codex.ids_session_types import CodexSessionId
from harness.impl.codex.model import CodexEffort, CodexModel


class TaskStartedPayload(BaseModel):
    """Represent task started payload."""

    model_config = FOREIGN
    type: Literal["task_started"] = "task_started"
    started_at: str | int | float | None = None
    turn_id: CodexTurnId | None = None
    collaboration_mode_kind: str | None = None
    model_context_window: int | None = None
    # Codex 0.156.x names the first turn of a turn chain (measured: a turn id).
    root_turn_id: CodexTurnId | None = None


class TaskCompleteError(BaseModel):
    """Represent task complete error."""

    model_config = FOREIGN
    message: str | None = None
    codex_error_info: str | None = None


class TaskCompletePayload(BaseModel):
    """Represent task complete payload."""

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
    """Represent collaboration mode settings."""

    model_config = FOREIGN
    model: CodexModel | None = None
    reasoning_effort: CodexEffort | None = None
    developer_instructions: str | None = None


class CollaborationMode(BaseModel):
    """Represent collaboration mode."""

    model_config = FOREIGN
    mode: str | None = None
    settings: CollaborationModeSettings | None = None


class ThreadSettingsBlock(BaseModel):
    """Represent thread settings block."""

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
    # Codex 0.156.x repeats the header's workspace roots and the turn context's
    # disabled plugins here (measured: the launch directory, and []).
    runtime_workspace_roots: list[str] | None = None
    disabled_plugin_ids: list[str] | None = None


class ThreadSettingsAppliedPayload(BaseModel):
    """Represent thread settings applied payload."""

    model_config = FOREIGN
    type: Literal["thread_settings_applied"] = "thread_settings_applied"
    thread_id: CodexSessionId | None = None
    thread_settings: ThreadSettingsBlock | None = None
