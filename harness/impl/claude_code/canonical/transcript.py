# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide the transcript module."""

# harness/impl/claude_code/canonical/transcript.py — Claude Code transcript PARSING.
#
# The parse half of the substream's parse/paint split (docs/sessionapi.md).
# This module is the ONE owner of the Claude Code transcript JSONL record
# shapes — reader AND writer: the type/user/assistant discrimination, the
# teammate-message unwrapping, the content-block walk, the tool_result text
# normalisation — for BOTH a subagent's transcript (subagents/agent-<id>.jsonl)
# and the parent session's own transcript (the same record grammar); the one
# sanctioned WRITE is set_session_title()'s `agent-name` naming-record append
# (the dashboard's web rename). The presenter that consumes its records is
# substream_render.Renderer.handle_line — the mirror's styled paint. An agent's
# web view is that same mirror, scoped, so
# there is no second rendering of these records anymore: the uncapped drill-down
# timeline that used to live here — parsed per agent, styled nothing like the
# mirror, and drifting from it — is gone, and only agent_usage() still reads a
# whole agent transcript, for the two numbers the scoreboard prices.
#
# Re-encoding a transcript record shape anywhere else is a bug (styleguide
# single-owner table). parse_line() is pure (no I/O, no state); the only
# I/O here is agent_usage()/conversation()'s own file read and
# set_session_title()'s one-line append.
#
# parse_line(s) returns one record per JSONL line (None = nothing renderable):
#   {"kind": "bad", "raw": s}                       unparseable JSON
#   {"kind": "compact", "meta": {...}}              a compact_boundary system record
#   {"kind": "recap", "text": str}                  an away_summary system record —
#       Claude Code's "recap": the one-line summary of what happened while you
#       were away (auto after ~3min idle, or on-demand via /recap), stored as a
#       `type=system` `subtype=away_summary` line whose plain-text `content` is
#       the summary. Not a compaction (adds context, doesn't compress it)
#   Prompt records contain text, metadata, and a resumed-turn flag:
#       a user prompt (unstripped) —
#       a plain `user` string OR a `queued_command` attachment (the delivered
#       form of a message queued mid-turn; commandMode=="prompt" only).
#       `meta` means the record is shaped like a user turn but the HUMAN DID NOT
#       TYPE IT — Claude Code injected it (see _injected for the marks it reads).
#       Seen carrying `Stop hook feedback: …` (a Stop hook's
#       blocking output), a loaded skill's whole SKILL.md body, `Continue from
#       where you left off.` (a resume nudge), the `<local-command-caveat>`
#       wrapper, `[Request interrupted by user…]` (the cancel annotation),
#       the post-/compact summary (`This session is being continued from a
#       previous conversation…`), and TEAMMATE MAIL (`Another Claude session sent
#       a message:` wrapping a peer's <teammate-message> — the one shape with no
#       structural flag to read). The `<`-wrapped local-command ones never reach
#       here at all: they are the `slash_command` kind below; the bare-prose ones
#       are indistinguishable from a
#       real prompt WITHOUT this flag, which is why it is now carried rather
#       than dropped: the dashboard's focus mode promises "your prompt", and a
#       hook's feedback rendered as a YOU bubble is not it. session_title has always skipped isMeta rows for the same
#       reason — this makes that fact reusable instead of re-read per consumer.
#       `resumed` is the ONE flavour distinction on top of it: this injection
#       RESUMED a turn Claude Code had already ENDED (see _RESUMES_TURN), so the
#       reply in front of it was a turn's FINAL answer and not mid-turn prose.
#   Slash-command records contain the name, arguments, and original text:
#       a `/command` turn the human typed. `text` is it as TYPED (`/model opus`);
#       `name`/`args` are kept apart so a command that changes SESSION STATE can
#       also emit that state event. Claude Code writes such a turn as THREE
#       user-shaped records — see _CMD_STDOUT_RE — and this kind is the ONE
#       record they collapse to; the other two are dropped (return None)
#   {"kind": "teammsg", "sender": str, "body": str} an incoming teammate message
#   {"kind": "results", "blocks": [...], "tur": …, "texts": [str, ...]}
#       a user record carrying tool_result blocks (in order) — `tur` is the
#       line's toolUseResult sidecar; `texts` collects the line's plain text
#       blocks (a PARENT transcript's user turns arrive as text blocks in list
#       content — the mirror renderer deliberately ignores them, byte-identical
#       to the pre-split behavior; timeline() renders them)
#   {"kind": "assistant", "usage": dict|None, "model": str|None, "id": str|None,
#    "blocks": [("text", str) | ("tool", block), ...]}
#       one assistant message line — blocks preserve the content order; the
#       record is returned even with no content list (usage/turn tracking must
#       still run)
#   Monitor events contain the task, summary, and event text:
#       one EVENT from an armed Monitor — a line the watched command printed.
#       Attributable only through `task`: the per-event notification names the
#       monitor's TASK id and never its tool_use_id (measured, 2.1.233).
#   Monitor completion records contain the task, operation ID, and status:
#       the same monitor's stream ending, which does carry <tool-use-id> — so
#       the end is attributable on its own even when nothing remembers the arm.
from harness.impl.claude_code.canonical.transcript_assignment_scan import (
    assignment_call_before as assignment_call_before,
)
from harness.impl.claude_code.canonical.transcript_model_activity import (
    AssistantTranscriptRecord as AssistantTranscriptRecord,
    BackgroundCommandCompletedTranscriptRecord as BackgroundCommandCompletedTranscriptRecord,
    GoalTranscriptRecord as GoalTranscriptRecord,
    ResultsTranscriptRecord as ResultsTranscriptRecord,
    TeammateIdleTranscriptRecord as TeammateIdleTranscriptRecord,
    TeamMessageTranscriptRecord as TeamMessageTranscriptRecord,
)
from harness.impl.claude_code.canonical.transcript_model_commands import (
    CommandOutputTranscriptRecord as CommandOutputTranscriptRecord,
    SlashCommandTranscriptRecord as SlashCommandTranscriptRecord,
)
from harness.impl.claude_code.canonical.transcript_model_core import (
    BadTranscriptRecord as BadTranscriptRecord,
    CompactSummaryTranscriptRecord as CompactSummaryTranscriptRecord,
    CompactTranscriptRecord as CompactTranscriptRecord,
    PromptTranscriptRecord as PromptTranscriptRecord,
    TextTranscriptRecord as TextTranscriptRecord,
    TranscriptKind as TranscriptKind,
)
from harness.impl.claude_code.canonical.transcript_model_notifications import (
    ActorAssignmentFinishedTranscriptRecord as ActorAssignmentFinishedTranscriptRecord,
    MonitorEndedTranscriptRecord as MonitorEndedTranscriptRecord,
    MonitorEventTranscriptRecord as MonitorEventTranscriptRecord,
    TranscriptRecord as TranscriptRecord,
)
from harness.impl.claude_code.canonical.transcript_parser import parse_line as parse_line
from harness.impl.claude_code.canonical.transcript_result_text import result_text as result_text
from harness.impl.claude_code.canonical.transcript_teammates import (
    AGENT_SUBDIR as AGENT_SUBDIR,
    teammate_actor_id as teammate_actor_id,
    teammate_meta as teammate_meta,
)
from harness.impl.claude_code.canonical.transcript_tool_scan import (
    background_call as background_call,
    tool_call_before as tool_call_before,
)
from harness.impl.claude_code.canonical.transcript_turn_scan import prompt_turn_before as prompt_turn_before
from harness.impl.claude_code.canonical.transcript_user_text import (
    LEAD_TEAMMATE_ID as LEAD_TEAMMATE_ID,
    classify_user_text as classify_user_text,
    teammate_idle_notifications as teammate_idle_notifications,
)
