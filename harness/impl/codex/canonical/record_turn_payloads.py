# Copyright (c) 2026 Zhambyl Yermagambet
"""Own turn payloads models."""

from __future__ import annotations

from pydantic import BaseModel

from harness.impl.codex.canonical.record_config import FOREIGN, OPEN_FOREIGN, ForeignMetadata
from harness.impl.codex.canonical.record_task_payloads import CollaborationMode
from harness.impl.codex.canonical.record_usage_payloads import TokenUsageRecordPayload
from harness.impl.codex.ids_conversation_types import CodexResponseId, CodexTurnId
from harness.impl.codex.model import CodexEffort, CodexModel


class TurnContextPayload(BaseModel):
    """Represent turn context payload.

    A `turn_context` top-level record. `model`/`effort`/
        `collaboration_mode.settings.reasoning_effort` are the only fields
        rollout._turn_context reads; the rest are real (a live codex-cli 0.147.0
        rollout) but unread — declared because `extra="forbid"` demands every
        field codex sends, not only the ones used. The deep policy trees
        (sandbox/permission/file-system) are GENUINELY open (module header,
        OPEN_FOREIGN in spirit, `dict[str, JsonValue]` in practice): a vendor
        policy DSL nothing here has ever read one field of.
    """

    model_config = FOREIGN
    model: CodexModel | None = None
    effort: CodexEffort | None = None
    collaboration_mode: CollaborationMode | None = None
    turn_id: CodexTurnId | None = None
    root_turn_id: CodexTurnId | None = None
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
    # Codex 0.156.x lists the plugins that the session turned off (measured: []).
    disabled_plugin_ids: list[str] | None = None


class CompactedContentPart(BaseModel):
    """Represent compacted content part."""

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
    """Represent compacted payload."""

    model_config = FOREIGN
    message: str | None = None
    replacement_history: list[CompactedHistoryItem] | None = None
    guardian_history: list[CompactedHistoryItem] | None = None
    retained_context: ForeignMetadata | None = None
    compaction_response_id: CodexResponseId | None = None
    latest_token_usage_record: TokenUsageRecordPayload | None = None
    window_id: str | int | None = None
    previous_window_id: str | int | None = None
    first_window_id: str | int | None = None
    window_number: int | None = None
