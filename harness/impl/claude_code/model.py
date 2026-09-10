# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide the model module."""

# harness/impl/claude_code/model.py — model / effort / context-window resolution
# for agents (historical name: claude_model.py — that compat shim is deleted).
#
# Extracted from claude-substream.py, where ~250 lines of config-dir walking,
# frontmatter parsing, and window tables had accreted (CLAUDE.md had always
# described this responsibility as shared-module territory). Everything here is
# a PURE function of its arguments plus the environment — no per-agent globals —
# so the substream (and anything else that needs to answer "what model/effort/
# window is this agent actually running?") composes these.
#
# Background, in one place:
# - There is NO context-size frontmatter field (docs): the window follows the
#   resolved MODEL, which an agent can pin explicitly (e.g. `model: opus[1m]`).
#   Sonnet 5 / Fable 5 / Opus 4.6-4.8 run 1M by default (no suffix), older
#   models are 200k unless [1m], and CLAUDE_CODE_DISABLE_1M_CONTEXT caps all.
# - Effort is NOT recorded in any transcript — it's config-only, resolved
#   env > agent-def frontmatter `effort` > session `effortLevel` > the model's
#   own default (docs: high on Opus 4.8/4.6 / Sonnet 5 / Sonnet 4.6 / Fable 5,
#   xhigh on Opus 4.7). A session-only `/effort` isn't persisted, so it can't
#   be seen here.
import time
from enum import StrEnum
from pathlib import Path

from pydantic import ValidationError

from core import env as environment
from domain.ids import ActorId
from harness.impl.claude_code.canonical import records

# How much of a transcript's tail session_model() scans for the last assistant
# turn: the latest turn is near the end, so a bounded read stays cheap even on
# long sessions.
STANDARD_CONTEXT_WINDOW = 200_000
LARGE_CONTEXT_WINDOW = 1_000_000
METADATA_READ_ATTEMPTS = 6
METADATA_RETRY_SECONDS = 0.05


class ClaudeCodeModel(StrEnum):
    """Represent claude code model."""

    FABLE = "fable"
    OPUS = "opus"
    SONNET = "sonnet"
    HAIKU = "haiku"
    CLAUDE_FABLE_FIVE = "claude-fable-5"
    CLAUDE_FABLE_FIVE_ONE = "claude-fable-5-1"
    CLAUDE_OPUS_FIVE = "claude-opus-5"
    CLAUDE_OPUS_FOUR_EIGHT = "claude-opus-4-8"
    CLAUDE_OPUS_FOUR_SIX = "claude-opus-4-6"
    CLAUDE_SONNET_FIVE = "claude-sonnet-5"
    CLAUDE_HAIKU_FOUR_FIVE = "claude-haiku-4-5"
    CLAUDE_HAIKU_FOUR_FIVE_OCTOBER = "claude-haiku-4-5-20251001"


CLAUDE_CODE_MODELS = (
    ClaudeCodeModel.FABLE,
    ClaudeCodeModel.OPUS,
    ClaudeCodeModel.SONNET,
    ClaudeCodeModel.HAIKU,
    ClaudeCodeModel.CLAUDE_FABLE_FIVE,
    ClaudeCodeModel.CLAUDE_FABLE_FIVE_ONE,
    ClaudeCodeModel.CLAUDE_OPUS_FIVE,
    ClaudeCodeModel.CLAUDE_OPUS_FOUR_EIGHT,
    ClaudeCodeModel.CLAUDE_OPUS_FOUR_SIX,
    ClaudeCodeModel.CLAUDE_SONNET_FIVE,
    ClaudeCodeModel.CLAUDE_HAIKU_FOUR_FIVE,
    ClaudeCodeModel.CLAUDE_HAIKU_FOUR_FIVE_OCTOBER,
)


class ClaudeCodeEffort(StrEnum):
    """Represent claude code effort."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"
    MAX = "max"


FABLE_MODEL = ClaudeCodeModel.FABLE.value
OPUS_MODEL = ClaudeCodeModel.OPUS.value
SONNET_MODEL = ClaudeCodeModel.SONNET.value
HAIKU_MODEL = ClaudeCodeModel.HAIKU.value


DISABLE_MILLION_CONTEXT = bool(
    environment.env_int("CLAUDE_CODE_DISABLE_1M_CONTEXT", 0),
)
# Substrings of real model ids. Opus 5 (like Sonnet 5 / Fable 5) has NO 200k
# variant — 1M is both its default and its maximum — so a PINNED `claude-opus-5`
# must resolve here; only the bare `opus` alias below covered it before, and the
# id is what the transcript records (the ctx bars read 5x over on a 200k window).
KNOWN_MILLION_MODELS = (
    "fable-5",
    "sonnet-5",
    "opus-5",
    "opus-4-6",
    "opus-4-7",
    "opus-4-8",
    "sonnet-4-6",
)


def window(model: str | None) -> int | None:
    """Return the window.

    A model alias / id (with or without [1m]) -> its context window; None if
        empty (so a caller can fall through a precedence list).

    Returns:
        Window.

    """
    if not model:
        return None
    normalized_model = model.lower().strip()
    if HAIKU_MODEL in normalized_model:
        return STANDARD_CONTEXT_WINDOW
    has_large_window = (
        "[1m]" in normalized_model
        or any(model_marker in normalized_model for model_marker in KNOWN_MILLION_MODELS)
        or normalized_model in {OPUS_MODEL, SONNET_MODEL, FABLE_MODEL}
    )
    return LARGE_CONTEXT_WINDOW if has_large_window else STANDARD_CONTEXT_WINDOW


def context_window(*models: str | None) -> int:
    """Return the context window.

    The context window for the first of `models` that resolves (a precedence
        list, best-known-first); 200k when none do or the 1M kill-switch is set.

    Returns:
        Context window.

    """
    if DISABLE_MILLION_CONTEXT:
        return STANDARD_CONTEXT_WINDOW
    for model in models:
        model_window = window(model)
        if model_window:
            return model_window
    return STANDARD_CONTEXT_WINDOW


def context_used(usage: records.MessageUsage) -> int:
    """Return the context used.

    The occupied context window from ONE assistant message's usage dict:
        every input token the model saw — fresh + just-cached + replayed-from-cache.
        output_tokens is excluded (what the model produced back, not context). 0
        when usage is absent/malformed. The ONE owner of this arithmetic
        (styleguide table) — the substream's ctx tag/footer and
        transcript.context_probe (the dashboard's saturation chips) both call it.

    Returns:
        Context used.

    """
    return sum(
        int(token_count or 0)
        for token_count in (
            usage.input_tokens,
            usage.cache_creation_input_tokens,
            usage.cache_read_input_tokens,
        )
    )


def agent_meta(
    tpath: str,
    agent_id: ActorId,
) -> records.AgentMetaFile:
    """Return the agent meta.

    The agent's meta.json sidecar (present at SubagentStart for teammates; may
        lag a beat for ordinary subagents, so retry briefly). Carries
        `customAgentType` — the DEFINITION's name, which for a teammate differs from
        its short display type (agentType "container" vs def "task-container") — and
        its configured `model`. An empty AgentMetaFile when it never appears.

    Returns:
        Agent meta.

    """
    base = tpath.removesuffix(".jsonl")
    metadata_path = Path(base) / "subagents" / f"agent-{agent_id}.meta.json"
    for _ in range(METADATA_READ_ATTEMPTS):
        metadata, retry = _read_agent_meta(metadata_path)
        if metadata is not None:
            return metadata
        if retry:
            time.sleep(METADATA_RETRY_SECONDS)
        else:
            break
    return records.AgentMetaFile()


def _read_agent_meta(metadata_path: Path) -> tuple[records.AgentMetaFile | None, bool]:
    try:
        with metadata_path.open(encoding="utf-8") as source:
            return records.AgentMetaFile.model_validate_json(source.read()), False
    except FileNotFoundError:
        # A missing file can appear after the subagent starts.
        return None, True
    except ValidationError as error:
        # A partial JSON document can become valid after its writer finishes.
        if any(detail["type"] != "json_invalid" for detail in error.errors()):
            raise
        return None, True
    except OSError:
        # Permissions and missing mounts do not improve during a short retry.
        return None, False
