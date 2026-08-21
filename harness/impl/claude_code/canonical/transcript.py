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
#   {"kind": "prompt", "text": str, "meta": bool, "resumed": bool}
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
#   {"kind": "slash_command", "name": str, "args": str, "text": str}
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
#   {"kind": "monitor_event", "task": str, "summary": str, "event": str}
#       one EVENT from an armed Monitor — a line the watched command printed.
#       Attributable only through `task`: the per-event notification names the
#       monitor's TASK id and never its tool_use_id (measured, 2.1.233).
#   {"kind": "monitor_ended", "task": str, "operation_id": str, "status": str}
#       the same monitor's stream ending, which does carry <tool-use-id> — so
#       the end is attributable on its own even when nothing remembers the arm.
import html
import json
import os
import re
from typing import Any

from harness.models import TitleWriteOutcome
from repository.contract.titles import NativeSessionTitleRepository


# A message DELIVERED to a teammate appears in its transcript as a plain user
# record whose text is wrapped in <teammate-message teammate_id="<sender>" …>BODY
# </teammate-message> (the very first one is the lead's spawn prompt).
TEAMMSG = re.compile(r'^\s*<teammate-message\b([^>]*)>\s*(.*?)\s*</teammate-message>\s*$', re.S)
_TM_ID  = re.compile(r'teammate_id="([^"]*)"')

# A <task-notification> XML block. Read with plain tag scans rather than an XML
# parser: the blocks are small, fixed-shape, and produced by Claude Code (not
# user input).
#
# FOUR different facts ride this one channel — an agent finishing, a background
# command finishing, a monitor's event, a monitor's stream ending — and it
# arrives TWICE for each: once as a `queue-operation` enqueue and again as the
# `user` record that re-injects it into the conversation. The `user` copy is the
# single owner (`_task_notification`); the queue-operation copy is plumbing.
# Measured in claude-code 2.1.233, where every notification appeared in both
# shapes and the queue pair was always enqueue-then-dequeue.
_TASK_NOTE = re.compile(r'<task-notification>(.*?)</task-notification>', re.S)

# The summary prefixes that separate the four. Prose, because that is all the
# channel gives: only the background and monitor-ended notifications carry a
# <tool-use-id>, and only a monitor's per-event one carries an <event>.
BACKGROUND_SUMMARY_PREFIX = "Background command"
MONITOR_SUMMARY_PREFIX = "Monitor"


def _note_tag(xml: str, name: str) -> str | None:
    m = re.search(r'<%s>(.*?)</%s>' % (name, name), xml, re.S)
    return m.group(1).strip() if m else None


def _task_notification(content: str) -> dict[str, str | None]:
    """A <task-notification> block -> the one fact it carries.

    The single reader of this channel, so that a notification cannot be counted
    as two different things. The order of the tests is the order of how specific
    the raw event is: a background completion and a monitor event are each marked
    by something structural (their summary prefix, an <event> tag), and an
    agent's completion is what is left — it is the only one of the four with no
    mark of its own, so it cannot be recognised, only defaulted to."""
    m = _TASK_NOTE.search(content)
    xml = m.group(1) if m else content
    summary = _note_tag(xml, "summary") or ""
    if summary.startswith(BACKGROUND_SUMMARY_PREFIX):
        # A background Bash completion, NOT an agent's: the same channel
        # delivers both, and treating this as an assignment finish painted
        # phantom "Agent finished" blocks for plain background commands.
        return {
            "kind": "background_command_completed",
            "operation_id": _note_tag(xml, "tool-use-id") or "",
            "status": _note_tag(xml, "status") or "completed",
        }
    event = _note_tag(xml, "event")
    if event is not None:
        return {
            "kind": "monitor_event",
            "task": _note_tag(xml, "task-id") or "",
            "summary": summary,
            "event": event,
        }
    if summary.startswith(MONITOR_SUMMARY_PREFIX):
        return {
            "kind": "monitor_ended",
            "task": _note_tag(xml, "task-id") or "",
            "operation_id": _note_tag(xml, "tool-use-id") or "",
            "status": _note_tag(xml, "status") or "completed",
        }
    return {
        "kind": "actor_assignment_finished",
        "assignment_id": _note_tag(xml, "tool-use-id") or "",
        "actor_id": _note_tag(xml, "task-id"),
        "status": _note_tag(xml, "status") or "completed",
        "summary": summary,
        "result": html.unescape(_note_tag(xml, "result") or "") or None,
    }


def result_text(content: Any) -> str:  # loose: claude code JSON, wave 2 gives it a real shape
    """Normalise a tool_result's content (str | block | block list) to text."""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):       # a lone content block — normalise to a 1-list
        content = [content]
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict):
                t = b.get("type")
                if t == "text" or isinstance(b.get("text"), str):
                    parts.append(b.get("text", ""))
                elif t == "tool_reference":                 # ToolSearch result
                    parts.append("→ loaded tool: " + str(b.get("tool_name", "")))
                elif t == "image":
                    parts.append("[image]")
                else:                                        # unknown block -> show it
                    try:
                        parts.append(json.dumps(b, ensure_ascii=False))
                    except Exception:
                        parts.append(str(b))
            elif isinstance(b, str):
                parts.append(b)
        return "\n".join(p for p in parts if p)
    return str(content)


# Claude Code injects `<system-reminder>` blocks INTO the text it hands an agent —
# the addressable-teammates roster, the CLAUDE.md nudge, and friends. They are
# machinery, not the brief: a subagent's ⇢ prompt block opened with two nested
# reminders and the roster of every other agent before a word of the actual task
# ("why do I see system reminders of the subagents in the main mirror"). Stripped
# here rather than at the paint site because it is a fact about Claude Code's
# transcript text, which this module owns; nested/unclosed forms are handled by
# taking the OUTERMOST span non-greedily and then sweeping any stray tag left over.
_REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>\s*", re.S | re.I)
_REMINDER_TAG = re.compile(r"</?system-reminder>\s*", re.I)


# Claude Code's name for the session's OWN lead inside the teammate vocabulary —
# fixed, and stated to every teammate in its system prompt ("The team lead's name
# is \"team-lead\". Send updates and completion notifications to them.", measured in
# claude-code 2.1.234). It is an alias, not a participant: the lead already has a
# canonical actor of its own, so a reader that takes this id at face value invents
# a second one — a teammate nobody launched, permanently running, one per session.
# The first record of every teammate's transcript is its brief FROM this sender,
# which is how it was found.
LEAD_TEAMMATE_ID = "team-lead"


def classify_user_text(text: str) -> tuple[str, str, str | None]:
    """("teammsg", sender, body) for a wrapped teammate message, else
    ("prompt", text, None). `text` is the raw user content string."""
    m = TEAMMSG.match(text)
    if m:
        sid = _TM_ID.search(m.group(1))
        return "teammsg", (sid.group(1) if sid else ""), m.group(2)
    return "prompt", text, None


# The trailing config hint Claude Code appends to every recap `content` — it
# points at the terminal's `/config` menu, irrelevant in the web bubble.
_RECAP_HINT = re.compile(r"\s*\(disable recaps in /config\)\s*$")


def _strip_recap_hint(text: str) -> str:
    """A recap's `content` minus the trailing "(disable recaps in /config)"
    hint. An empty result stays empty (parse_line drops it)."""
    return _RECAP_HINT.sub("", text).strip()


# The TEAMMATE-MAIL WRAPPER — Claude Code delivering another session's message
# as a user turn of ITS OWN making: a framing sentence, the peer's
# <teammate-message> block(s), then Claude Code's own trailing "This came from
# another Claude session … that's permission laundering" instruction. Nothing in
# it was typed by the human, so it is `meta` like any other injection.
#
# This is the ONE mark _injected reads out of TEXT, because the record carries no
# structural flag whatsoever (measured on the corpus: type "user", `isMeta`
# absent, `userType` "external", `isSidechain` false — byte-for-byte the shape of
# a typed prompt). So the pattern is ANCHORED at the start of the content: a
# message that merely QUOTES a wrapper — a paste asking "why is this in my
# transcript?", this repo's own docs about it — has something in front of it and
# stays a prompt. That is the same false-positive class _injected refuses to court
# for the interrupt marker; an anchor is what makes text safe to read here.
#
# The bare `<teammate-message>…` form (no wrapper) is NOT this: classify_user_text
# already turns it into a `teammsg` record with its own ✉ sender bubble. A wording
# change in the framing sentence degrades to today's behaviour (a YOU bubble), not
# to a crash.
_TEAM_WRAPPER = re.compile(
    r'^\s*Another Claude session sent a message:\s*<teammate-message\b')


def _injected(o: dict[str, Any], text: str = "") -> bool:  # loose: claude code JSON, wave 2 gives it a real shape
    """Whether this user-shaped record was written by CLAUDE CODE rather than
    typed by the human — the `meta` flag on the prompt/results records below.
    Three structural marks plus one anchored text shape (`text`, the record's
    content when it is a plain string — see _TEAM_WRAPPER):

      isMeta               a Stop hook's blocking feedback, a loaded skill's
                           whole SKILL.md body, the `Continue from where you
                           left off.` resume nudge;
      interruptedMessageId the synthetic `[Request interrupted by user…]`
                           annotation, which carries the id of the message it
                           cut off. It is NOT isMeta (measured across the
                           transcript corpus, both the bare and the `for tool
                           use` form), so it needed its own mark;
      isCompactSummary     the COMPACTION SUMMARY — the multi-thousand-word
                           "This session is being continued from a previous
                           conversation…" recap Claude Code writes after
                           /compact (or an auto-compaction) and replays as the
                           new context. It follows a `compact_boundary` system
                           record and is likewise NOT isMeta, so it too needed
                           its own mark.

    The ANNOTATION is deliberately not matched on its text, which would re-run
    the false-positive class tabstatus.is_interrupt_line documents at length: any
    growth that merely QUOTES the marker — a Read of a doc that mentions it, a
    grep hit, a conversation about it — is textually identical to the real
    thing, and the marker can appear anywhere in a record. The id-bearing/boolean
    fields cannot be quoted. The teammate wrapper has no such field to read and
    is instead pinned to the START of the content (see _TEAM_WRAPPER)."""
    return bool(o.get("isMeta") or o.get("interruptedMessageId")
                or o.get("isCompactSummary")
                or (text and _TEAM_WRAPPER.match(text)))


# The injections that RESUME a turn Claude Code had already ENDED — as opposed to
# the ones it writes MID-turn. Anchored at the start of the content, for exactly
# the reason _TEAM_WRAPPER is: the record carries no structural flag (measured —
# a Stop hook's feedback and a loaded skill's body are byte-for-byte the same
# user/isMeta shape, same `promptId`, and the only structural tell is on a
# DIFFERENT record: the `hook_blocking_error` attachment / `stop_hook_summary`
# system line that FOLLOWS it, which an incremental tail read ending on the
# injection would not have yet). Anchoring is what makes text safe to read: a
# turn that merely QUOTES the wording — this repo's own docs, a grep hit — has
# something in front of it and stays an ordinary injection.
#
# WHY the distinction exists: the dashboard's
# focus mode keeps ONE reply per turn, the one it ends on. A Stop hook fires
# BECAUSE the turn ended, so the reply in front of its feedback IS a final
# answer — and a Stop hook that nudges every turn (the aggregator-adapters wiki
# nudge) therefore hid every real result behind the "persisted the note" reply
# that followed it. A MID-turn injection (a loaded SKILL.md body, the post-
# /compact summary, teammate mail) is deliberately NOT in here: the prose before
# one of those is running commentary, and treating it as a boundary would
# manufacture an extra "final" reply per injection.
#
# `Continue from where you left off.` (the resume nudge) is likewise not here,
# though it has the same shape: it follows a turn that ended in the PREVIOUS
# session view, whose reply focus mode already showed as final under its own
# prompt. Add a wording here only with a transcript to point at.
_RESUMES_TURN = (
    re.compile(r"^\s*Stop hook feedback:"),
)


# Every `kind` parse_line can return — the record vocabulary of this module,
# declared in one place so a reader can see the whole of it, and so that adding
# a kind is a visible act rather than one more branch somewhere.
KINDS = ("bad", "compact", "recap", "prompt", "teammsg", "results",
         "assistant", "monitor_event", "monitor_ended",
         "actor_assignment_finished", "background_command_completed", "goal")


def parse_line(s: str) -> dict[str, Any] | None:  # loose: claude code JSON, wave 2 gives it a real shape
    """One transcript JSONL line -> a typed record (see the module header)."""
    try:
        o = json.loads(s)
    except Exception:
        return {"kind": "bad", "raw": s}
    t = o.get("type")
    msg = o.get("message") or {}
    content = msg.get("content")
    if t == "system" and o.get("subtype") == "compact_boundary":
        return {"kind": "compact", "meta": o.get("compactMetadata") or {}}
    if t == "system" and o.get("subtype") == "away_summary":
        # Claude Code's recap — the away summary (see the module header). The
        # summary text is the system record's plain-string `content`; drop the
        # trailing "(disable recaps in /config)" config hint, which points at a
        # terminal-only menu and is noise in the dashboard bubble.
        text = _strip_recap_hint(o.get("content") or "")
        return {"kind": "recap", "text": text} if text else None
    if t == "system" and isinstance(o.get("content"), str):
        cleared_prefix = "Goal cleared:"
        if o["content"].startswith(cleared_prefix):
            return {
                "kind": "goal",
                "objective": o["content"][len(cleared_prefix):].strip() or None,
                "state": "cleared",
                "reason": None,
            }
    if t == "user":
        if isinstance(content, str):
            if not content.strip():
                return None
            if (o.get("origin") or {}).get("kind") == "task-notification":
                return _task_notification(content)
            kind, a, b = classify_user_text(content)
            if kind == "teammsg":
                return {"kind": "teammsg", "sender": a, "body": b}
            # The three records of a `/command` turn (see _CMD_STDOUT_RE): the
            # wrapper becomes ONE record carrying what the human typed, and the
            # caveat + the command's echoed stdout are dropped. Ordered before
            # the prompt return because that is the only thing they could
            # otherwise become.
            cmd_name, cmd_args = _command_wrapper(content)
            if cmd_name:
                return {"kind": "slash_command", "name": cmd_name, "args": cmd_args,
                        "text": _command_text(content)}
            if _CMD_CAVEAT_RE.match(content) or _CMD_STDOUT_RE.match(content):
                return None
            # isMeta = Claude Code injected this user turn (see the header) —
            # carried so consumers can tell it from something the human typed.
            # The content goes in too: the teammate-mail wrapper is injected
            # with no structural flag to show it (see _TEAM_WRAPPER).
            return {"kind": "prompt", "text": content,
                    "meta": _injected(o, content)}
        if isinstance(content, list):
            blocks: list[dict[str, Any]] = []  # loose: claude code JSON, wave 2 gives it a real shape
            texts: list[str] = []
            for blk in content:
                if not isinstance(blk, dict):
                    continue
                if blk.get("type") == "tool_result":
                    blocks.append(blk)
                elif blk.get("type") == "text" and (blk.get("text") or "").strip():
                    texts.append(str(blk.get("text")))
            if blocks or texts:
                # `meta` as on a plain prompt (see the header): a SKILL LOAD
                # arrives in exactly this shape — an isMeta user record whose
                # text block is the whole SKILL.md body ("Base directory for
                # this skill: …"), injected right after the Skill tool_result.
                # Without the flag conversation() rendered it as a YOU prompt
                # bubble holding the entire skill.
                return {"kind": "results", "blocks": blocks,
                        "tur": o.get("toolUseResult"), "texts": texts,
                        # the leading text block, for the one text-read mark
                        # (_TEAM_WRAPPER): a wrapper arriving in list form
                        # would be that block, and the mark is anchored anyway
                        "meta": _injected(o, texts[0] if texts else "")}
        return None
    if t == "assistant":
        assistant_blocks: list[tuple[str, Any]] = []  # loose: claude code JSON, wave 2 gives it a real shape
        if isinstance(content, list):
            for blk in content:
                if not isinstance(blk, dict):
                    continue
                if blk.get("type") == "text":
                    assistant_blocks.append(("text", blk.get("text", "")))
                elif blk.get("type") == "tool_use":
                    assistant_blocks.append(("tool", blk))
        u = msg.get("usage")
        return {"kind": "assistant", "usage": u if isinstance(u, dict) else None,
                "model": msg.get("model"), "id": msg.get("id"),
                "blocks": assistant_blocks}
    if t == "attachment":
        # A message typed while a turn is running is QUEUED by Claude Code and,
        # when the turn boundary delivers it, recorded ONLY as this
        # `queued_command` attachment — never as a plain `user` string (verified
        # across the transcript corpus). So a mid-turn queued message is the one
        # user prompt that never reaches conversation()/timeline() as a prompt:
        # the dashboard mirror silently drops it AND the composer's ⧗ chip never
        # drains (drainQueue matches a delivered prompt by text) — the "queued
        # message stuck / missing from the transcript" report. Surface it as a
        # prompt so both work. `commandMode` separates real prompts (human +
        # auto-continuation) from the `task-notification` re-injections (which
        # are harness noise, not user turns); conversation()'s own `<`-wrapper
        # filter still drops any command/caveat wrapper, same as a typed prompt.
        att = o.get("attachment") or {}
        if att.get("type") == "goal_status":
            objective = str(att.get("condition") or "").strip()
            if not objective:
                return None
            return {
                "kind": "goal",
                "objective": objective,
                "state": "completed" if att.get("met") is True else "active",
                "reason": str(att.get("reason") or "").strip() or None,
            }
        if att.get("type") == "queued_command" and att.get("commandMode") == "prompt":
            return {"kind": "prompt", "text": att.get("prompt") or ""}
        return None
    if t == "queue-operation":
        # The ENQUEUE half of a task-notification's delivery, and the same XML
        # the `user` record above carries — measured: every notification appeared
        # in both shapes. Read here too, it would double every monitor event.
        # The `user` copy owns it, because that copy is the one the model was
        # actually given and the one that carries `origin.kind`.
        return None
    return None


PROJECTS_DIR = "projects"   # ~/.claude/projects/<cwd-hash>/<sid>.jsonl — Claude
#                             Code's own on-disk transcript layout. Not a path
#                             this repo mints, so it is not core/paths' business;
#                             it is a FACT ABOUT CLAUDE CODE, which makes this
#                             module its owner (docs/styleguide.md)
AGENT_SUBDIR = "subagents"  # …/<sid>/subagents/agent-<id>.{jsonl,meta.json} —
#                             the per-agent sidecar dir agent_paths() derives


# --- session title + the main-thread conversation (dashboard read models) ----------

# The slash-command wrapper Claude Code stores for a `/command` turn (the
# `<command-name>/foo</command-name>` + optional `<command-args>bar</…>` tags in
# the user record's content; a skill invocation leads with `<command-message>`
# instead, so the name tag is SEARCHED for rather than anchored). session_title's
# LAST-resort fallback reads the command name back out so a short slash-command
# session gets `/foo` instead of a bare sid (docs/session-naming-findings.md,
# *Fallbacks*), and conversation() unwraps it into the prompt bubble the user
# actually typed (_command_parts is the one owner of the derivation).
#
# The args are deliberately NOT newline-free: a slash command's argument is
# whatever was in the input box, which is regularly several lines (a sid on one,
# the question on the next — measured on this repo's own /audit-debug turns). A
# `[^<\n]*?` args class matched NONE of those, so the whole argument — the entire
# message, as far as the human is concerned — was lost. `[^<]*?` still stops at
# the closing tag, and the surrounding `\s*` trims the indentation Claude Code
# puts in front of the tags without touching the interior.
_CMD_NAME_RE = re.compile(r"<command-name>\s*(/?[^<\n]+?)\s*</command-name>")
_CMD_ARGS_RE = re.compile(r"<command-args>\s*([^<]*?)\s*</command-args>")

# A `/command` turn is written as THREE user-shaped records, not one (measured on
# this repo's own `/model opus` turn, session 6a23d1c5): the `<local-command-caveat>`
# isMeta injection, the `<command-name>` wrapper, and the command's echoed
# `<local-command-stdout>`. Only the first carries a structural flag, so without
# these two marks the other two each became a `message.created` with role "user"
# — the user saw one system block and TWO "you" bubbles for one keystroke.
#
# Both are ANCHORED at the start of the content, for the reason _TEAM_WRAPPER is:
# a message that merely QUOTES a wrapper — a paste asking about it, this repo's
# own docs, a grep hit — has something in front of it and stays a prompt. The same
# anchor gates the wrapper itself (_command_wrapper): _command_parts SEARCHES for
# the name tag, which is right for a title fallback but would silently swallow a
# pasted message's text into a fake command bubble.
_CMD_STDOUT_RE = re.compile(r"^\s*<local-command-stdout>")
_CMD_CAVEAT_RE = re.compile(r"^\s*<local-command-caveat>")
_CMD_OPEN_RE = re.compile(r"^\s*<command-(?:message|name|args)>")


def _command_wrapper(s: str) -> tuple[str, str]:
    """`(name, args)` when `s` IS a slash-command wrapper Claude Code wrote —
    ('', '') otherwise. The anchored gate on top of _command_parts: the wrapper
    is the whole record and opens with one of its own tags, so a record with
    prose in front of the tag is a human's prompt about a command, not one."""
    if not _CMD_OPEN_RE.match(s):
        return "", ""
    return _command_parts(s)

TITLE_SCAN = 200        # head-window lines session_title inspects: summary records
#                         are PREPENDED on resume, so they precede the first prompt;
#                         a title must never cost a full multi-MB transcript read

TITLE_TAIL_B = 65536    # tail-window bytes session_title scans for the LAST naming
#                         record: `ai-title` rows are re-emitted every few turns, so
#                         the current one sits within lines of EOF — the bounded tail
#                         keeps the no-full-read rule while a mid-file `agent-name`
#                         in a >64KB transcript is the one accepted gap


def _command_parts(s: str) -> tuple[str, str]:
    """`(name, args)` of the `<command-name>`/`<command-args>` wrapper in `s` —
    ('', '') when it carries no command name. The ONE owner of that derivation:
    the title ladder wants a single LINE of it and conversation() wants the args
    VERBATIM, which is the whole reason the parse is separated from either
    rendering (docs/styleguide.md, *Single-owner vocabulary*).

    Presence of the name tag is also what tells a user-typed command turn from
    the two OTHER `<`-wrapped local-command records, neither of which carries one
    (measured across the corpus): `<local-command-caveat>` is Claude Code's
    isMeta injection, and `<local-command-stdout>` is the command's own echoed
    OUTPUT. Both must stay dropped, so this gate is load-bearing — do not relax
    it to "starts with <command"."""
    m = _CMD_NAME_RE.search(s)
    if not m:
        return "", ""
    name = m.group(1).strip()
    if not name:
        return "", ""
    a = _CMD_ARGS_RE.search(s)
    return name, (a.group(1).strip() if a else "")


def _command_text(s: str) -> str:
    """The slash-command turn as the user TYPED it — `/foo` plus its argument
    verbatim, newlines and all — the text of parse_line's `slash_command` record
    and so of the prompt bubble the dashboard shows. '' when `s` is not a command
    wrapper (see _command_parts for what else wears `<`). The ONE owner of that
    rendering; _command_label is its collapsed single-line twin, for titles."""
    name, args = _command_parts(s)
    if not name:
        return ""
    return ("%s %s" % (name, args)) if args else name


def _jsonl_file(path: str) -> bool:
    """A .jsonl that EXISTS ON DISK — the shared precondition of the two layout
    predicates below. A missing file is never ours: rename must never CREATE a
    transcript just to name it, and a path with nothing behind it tells the
    read fan-outs nothing they could act on."""
    return bool(path) and path.endswith(".jsonl") and os.path.isfile(path)


def _session_transcript(path: str) -> bool:
    """`…/projects/<hash>/<sid>.jsonl` — a Claude Code SESSION transcript. The
    ONE spelling of that layout (owns/renameable both go through it)."""
    return _jsonl_file(path) and os.path.basename(
        os.path.dirname(os.path.dirname(path))) == PROJECTS_DIR


def _agent_transcript(path: str) -> bool:
    """`…/projects/<hash>/<sid>/subagents/agent-<id>.jsonl` — a session
    transcript's per-AGENT sidecar (the agent_paths layout). Ours to parse, but
    not a session: nothing about it is renameable."""
    d = os.path.dirname(path or "")
    return _jsonl_file(path) and os.path.basename(d) == AGENT_SUBDIR and \
        os.path.basename(os.path.dirname(os.path.dirname(
            os.path.dirname(d)))) == PROJECTS_DIR


CLAIM_HEAD_B = 8192   # head bytes owns() reads when the LAYOUT doesn't settle it

# The raw `type` values a Claude Code transcript record can carry — the
# discrimination parse_line() does, as a set, so owns() can recognise one of our
# files by its FIRST record instead of its directory. Only top-level types: a
# codex rollout's records are session_meta/response_item/turn_context/event_msg
# and its inner `payload.type` is never read here, so the two vocabularies
# cannot collide.
RECORD_TYPES = frozenset((
    "summary", "user", "assistant", "system", "attachment",
    "queue-operation", "agent-name", "ai-title", "file-history-snapshot",
))


def _claude_head(path: str) -> bool:
    """Does the file's HEAD hold a record only Claude Code writes? The content
    half of owns(), for a transcript that is ours but not where we expect (a
    relocated CLAUDE_CONFIG_DIR, a copied file, a fixture). Bounded to
    CLAIM_HEAD_B and torn-line safe — an unparseable line is simply not
    raw event. False on any OSError: unreadable is not ours to claim."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(CLAIM_HEAD_B)
    except OSError:
        return False
    for raw in head.split(b"\n"):
        if not raw.strip():
            continue
        try:
            o = json.loads(raw)
        except Exception:
            continue                    # noise, or the head's torn last line
        if isinstance(o, dict) and o.get("type") in RECORD_TYPES:
            return True
    return False


def owns(path: str) -> bool:
    """Is `path` a file this plugin SPEAKS — the `owns` provider behind
    plugins._first_path (the ownership gate on every path-keyed read fan-out)
    and plugins.owns_by (the dashboard's resume guard)? True for a session
    transcript, for one of its agent sidecars, and for any other .jsonl whose
    head carries a Claude record; False for everything else, a codex rollout
    above all.

    It exists because first-plugin-wins is first-PARSER-wins, and these parsers
    are BOUNDED and FAIL OPEN by design: prompt_count returns its cap for any
    file over PROMPT_SCAN_B without reading a byte, which is right for a large
    Claude transcript and nonsense for the 429KB codex rollout it measured 8
    human prompts in. A parser that cannot read the whole file cannot always
    tell whose file it is. The two things that can are the LAYOUT (a stat, and
    what every real session has) and the FIRST RECORD (a bounded head read) —
    layout first, because it costs no read and is the case that always holds;
    the head is the fallback that keeps a transcript living somewhere unusual
    from going silently unowned. Neither is the whole file: ownership must stay
    affordable to ask once per session per poll."""
    return (_session_transcript(path) or _agent_transcript(path)
            or (_jsonl_file(path) and _claude_head(path)))


def renameable(path: str) -> bool:
    """Does this plugin own `path` as a RENAMEABLE Claude session transcript
    (`…/projects/<hash>/<sid>.jsonl`, present on disk)? The ONE gate both
    rename channels ask: `set_session_title` below, before appending the
    record itself, and the dashboard's LIVE path, before pasting Claude Code's
    own `/rename` into the window — a codex standalone host's window carries
    the same `claude_session` tag (harness/impl/codex/session.py) but its
    transcript_path is a codex ROLLOUT, which must receive neither.

    Narrower than owns() on purpose: an agent sidecar is ours to READ and has
    no session name to write."""
    return _session_transcript(path)


def set_session_title(path: str, name: str) -> bool | None:
    """Append the `agent-name` naming record — the /rename channel `_title_records`
    parses back (docs/session-naming-findings.md §2) — to a Claude session
    transcript: the web rename's write half FOR A PARKED SESSION. True on
    success; None when `path` is not a Claude session transcript (`renameable`
    above). OSError propagates — the caller (dashboard post_rename) turns it
    into a 502 + A.error; this is a user-facing request/reply path, not a hook,
    so no swallow here. `sessionId` derives from the FILENAME stem, not the
    caller's sid — an adopt/fork chain's current sid differs from the
    transcript's own (the findings doc: "sessionId must match the filename").

    NEVER call this on a LIVE session (docs/session-naming-findings.md §4, the
    2026-07-29 finding): Claude Code re-emits its own in-memory `agent-name`
    at every turn boundary, so a record it did not write is overwritten within
    one turn. A live rename goes through the TUI's own `/rename`."""
    if not renameable(path):
        return None
    sid = os.path.basename(path)[:-len(".jsonl")]
    rec = json.dumps({"type": "agent-name", "agentName": name,
                      "sessionId": sid}, ensure_ascii=False)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(rec + "\n")                # ONE write: atomic O_APPEND line
    return True


class TranscriptTitleRepository(NativeSessionTitleRepository):
    """A `NativeSessionTitleRepository` over the transcript itself.

    The codex twin keeps its titles in a sqlite index; Claude Code keeps them
    in the transcript, as a naming record it re-reads. Same operation, entirely
    different store — which is the whole reason this is a Protocol.
    """

    def renameable(self, source_reference: str) -> bool:
        return bool(renameable(source_reference))

    def set_title(self, source_reference: str, title: str) -> TitleWriteOutcome:
        if not self.renameable(source_reference):
            return "unsupported"
        try:
            written = set_session_title(source_reference, title)
        except OSError:
            return "unavailable"
        return "renamed" if written else "unavailable"


titles = TranscriptTitleRepository()
