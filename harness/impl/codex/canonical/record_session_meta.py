# Copyright (c) 2026 Zhambyl Yermagambet
"""Own session meta models."""

from __future__ import annotations

from pydantic import BaseModel

from harness.impl.codex.canonical.record_config import FOREIGN, OPEN_FOREIGN, ForeignMetadata
from harness.impl.codex.canonical.record_session_sources import (
    SessionMetaBaseInstructions,
    SessionMetaContextWindow,
    SessionMetaHistoryBase,
    SessionMetaSource,
)
from harness.impl.codex.ids_session_types import CodexActorId, CodexSessionId


class SessionMetaGit(BaseModel):
    """Represent session meta git.

    The repository facts codex stamps on a session — `{}` outside a repo,
        `{commit_hash, branch, repository_url}` inside one (both measured, real
        local rollouts).
    """

    model_config = FOREIGN
    commit_hash: str | None = None
    branch: str | None = None
    repository_url: str | None = None


class SessionMetaPayload(BaseModel):
    """Represent session meta payload.

    A `session_meta` record's `payload` — read by sources.py (rollout
        ownership / parent-thread discovery) and translator.py (actor naming).
        Most fields below (session_id, cli_version, model_provider, …) are real,
        measured (a live codex-cli 0.147.0 rollout) but read by NOTHING here —
        declared anyway because `extra="forbid"` demands it of every field codex
        actually sends, not only the ones this translator uses.
    """

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
    forked_from_ordinal_exclusive: int | None = None
    multi_agent_version: str | None = None
    subagent_history_start_ordinal: int | None = None
    # Codex 0.156.x names the workspace roots of the session (measured: the
    # launch directory). A refused header hides the whole rollout.
    runtime_workspace_roots: list[str] | None = None


class CodexHookPayload(BaseModel):
    """Represent codex hook payload.

    A codex hook delivery's JSON body — GENUINELY open (module header,
        OPEN_FOREIGN): unlike a rollout record, a hook delivery's field set varies
        by `hook_event_name` (SessionStart/PreCompact/PostCompact/…), most of
        which this translator never reads and has no fixture corpus to declare
        exhaustively. Declared as far as reality allows: the seven fields
        translator._translate_hook actually reads.
    """

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
