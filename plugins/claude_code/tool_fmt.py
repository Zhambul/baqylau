# plugins/claude_code/tool_fmt.py — the MAIN agent's GENERIC tool one-liner.
# Entry point: claude-tool-fmt.py (a thin shim — the entry FILENAME is the audit vocabulary).
#
# Every tool the main agent calls that no other formatter owns — WebFetch,
# WebSearch, ToolSearch, Grep, Glob, EnterWorktree, ScheduleWakeup, whatever
# Claude Code ships next — as ONE quiet expandable line:
#
#   terminal:  · WebFetch(https://docs.claude.com/en/docs/claude-code/hooks)
#              …click it and the request + the answer expand in place
#   web:       the same line, `data-v`-clickable, act="tool"
#
# Until this existed those calls rendered NOTHING: the dispatcher's PostToolUse
# table had a matcher for Bash, the file tools, Monitor, SendMessage, Skill and
# the task tools, and everything else fell through (measured — session 5a8123c7's
# hook_events carry WebSearch/WebFetch rows with no formatter decision beside
# them). A CHILD agent's generic tool calls have been rendered all along
# (core/agentblocks.AgentStream.tool_open, in this same `·` register), so the
# mirror showed a subagent's web fetch and not the lead's.
#
# WHICH TOOLS: the routing matcher is an EXCLUSION, not an allowlist (see
# dispatch._GENERIC_TOOL) — a tool nobody has taught us about renders by DEFAULT
# and the enumeration is of what is already SPOKEN FOR. An allowlist would have
# to be edited every time Claude Code ships a tool, and the failure mode is
# silence, which is exactly the bug this fixes. The handler re-checks the same
# exclusion (SKIP below) because it also runs standalone as bin/claude-tool-fmt.py.
#
# THE SHAPE is a `line` op, like a file one-liner and unlike a child's tool
# BLOCK: the lead's mirror is a dense stream of its own work, and a header + a
# request gutter + a result gutter per WebFetch would bury the commands around
# it. So the request is summarised into the parens and everything — the full
# request AND the response — goes behind the click, through the same
# `view:<tool_use_id>` stash + OSC 8 hyperlink a Read expands with
# (core.copy.stash, docs/click-to-view.md).
from core import agentblocks as AB
from core import copy as C
from core import ops as O
from core import render as R
from core import state as ST
from core import streamfmt as SF
from plugins.claude_code import hookkit as H
from plugins.claude_code import transcript as TR

A = O.A    # audit trail (real module, or a no-op stub if it failed to import)

TOOL_RGB = O.SLATE     # the main session's quiet register (its command colour)
FAIL_RGB = O.RED       # …and the shared failure colour

# Tools that reach this handler and are deliberately NOT rendered. Kept SHORT
# and argued, one entry at a time — the default is to render:
#   TaskList / TaskGet — a read-back of the session's OWN task bookkeeping,
#   which is already a pinned card on the web and a ✚/✓ row in the mirror
#   (task_fmt.py). They change nothing and hold nothing a click could show, so
#   they would be pure repetition of a surface that is already there.
# (Everything the dispatcher's matcher already excludes — Bash, the file tools,
# Monitor, SendMessage, Skill, the modals, Task/Agent — is listed THERE, beside
# the routes it is the complement of; this set is only for the standalone path
# and for tools that reach us but say nothing.)
SKIP = frozenset(("TaskList", "TaskGet"))

# The one-liner's request excerpt, in display cells — enough for a URL or a
# search query, short enough that the line stays a line. The WHOLE request is
# behind the click, so this is an excerpt and not a budget.
CAP_REQ = 72
# …and the expansion's two halves, in LINES (core/streamfmt.cap). Generous but
# bounded, deliberately UNLIKE a file view (file_fmt keeps a Read's body whole —
# the file is the thing you asked for). A tool answer has no such natural end: a
# WebFetch of a documentation page is the page, and the click asked what the
# tool ANSWERED, which the head of it says.
CAP_REQ_VIEW = 40
CAP_BODY = 400
# …plus a CHARACTER ceiling, because a line cap alone doesn't bound this one: a
# response field that isn't a string is json-dumped, so a WebSearch's `results`
# list arrives as ONE line that can be hundreds of KB. The stash is a state-DB
# row parked with the session forever, and no reader wants a wall of JSON.
CAP_CHARS = 20000
_MORE = "\n… (truncated)"


def _fields(obj):
    """A tool payload -> text. A dict (which is what every measured
    tool_input AND tool_response is — `{"url":…, "prompt":…}`,
    `{"bytes":…, "code":…, "result":…}`) becomes the compact `key: value`
    listing its owner already renders for a request (transcript.input_summary);
    anything else goes through the tool_result normaliser beside it
    (transcript.result_text — a string, a content block, a list of them).

    Two owners, one dispatch: the shapes are Claude's, transcript.py owns them,
    and this module invents no third rendering of them."""
    if isinstance(obj, dict):
        return TR.input_summary(obj)
    return TR.result_text(obj)


def _capped(text, lines):
    """`text` bounded BOTH ways — by lines (streamfmt.cap, the shared marker)
    and then by characters, since one json-dumped field can be a single
    enormous line that no line cap touches."""
    out = SF.cap(text, lines)
    return out if len(out) <= CAP_CHARS else out[:CAP_CHARS] + _MORE


def request_brief(inp):
    """The tool's request as ONE short line for the parens — the FIRST field of
    the request with its key dropped: a WebFetch's url, a WebSearch's or
    ToolSearch's query, a Grep's pattern, an EnterWorktree's path (measured:
    every one of those payloads leads with the field that names the request).
    Empty when the tool takes no input.

    Deliberately not a per-tool table of "the interesting key": that table would
    need a row per tool, which is the enumeration this whole formatter exists to
    avoid, and a wrong guess costs a word on a line whose click shows every
    field anyway."""
    first = _fields(inp).split("\n")[0].strip()
    _, sep, val = first.partition(": ")
    return R.fit(" ".join((val if sep else first).split()), CAP_REQ)


def view_block(name, brief, ti, tr, failed=False):
    """The click-to-view block for one tool call: the request in full, then what
    the tool answered (or the error it answered with — for a tool the failure
    text IS the content, which is why this stashes on the failure path too,
    where a file op has nothing to show and doesn't).

    Returns a list of paint ops (JSON-clean — what the renderer paints in place
    and what claude-copy.py toggles), or None when there is nothing behind the
    click at all, in which case the line stays plain and unclickable."""
    rgb = FAIL_RGB if failed else TOOL_RGB
    req = _capped(_fields(ti).strip(), CAP_REQ_VIEW)
    body = _capped(_fields(tr).strip(), CAP_BODY)
    if not (req or body):
        return None
    hdr = name + ((" · " + brief) if brief else "")
    ops = [O.rule(), O.label(hdr, rgb)]
    if req:
        ops.append(O.gut(req, rgb))
    ops.append(O.gut(body, rgb) if body else O.gut(SF.no_output_body(), rgb))
    if failed:
        ops.append(O.gut(R.fg(*O.RED) + AB.fail_text() + R.RST, rgb))
    return ops + [O.blank()]


def tool_line(name, brief, failed=False):
    """`· WebFetch(https://…)` — the one-liner's text. The glyph is the shared
    one a child's generic tool block opens with (core/agentblocks.TOOL_GLYPH),
    so the lead's tool call and an agent's read as the same KIND of thing and
    the web classifier's fallback reads one spelling; the parens are the file
    one-liner's anatomy (streamfmt.file_line), so it reads as a sibling of
    `Read(x.py)` — which is what it is: a one-line summary with the content
    behind the click."""
    col = R.fg(*(FAIL_RGB if failed else TOOL_RGB))
    line = col + AB.TOOL_GLYPH + " " + name + R.RST
    if brief:
        line += R.DIM + "(" + R.COL["def"] + brief + R.DIM + ")" + R.RST
    return line


def main():
    d, LOG = H.read_payload()
    if d is None:
        return
    name = (d.get("tool_name") or "").strip()
    if not name:
        return H.ignore(d, "no tool name")
    if name in SKIP:
        return H.ignore(d, "quiet tool (%s)" % name)
    if d.get("agent_id"):
        # a subagent's own tool call — the substream owns that stream's rendering
        # (agentblocks.tool_open paints it there); handling both duplicates the row
        return H.ignore(d, "subagent event (agent_id present)")
    if ST.parked(LOG):
        # no live state DB = unhosted (headless/daemon) or already parked — emitting
        # would CREATE the DB whose file-existence is the session-alive signal
        return H.ignore(d, "no state DB (unhosted session)")
    ti = d.get("tool_input") or {}
    tr = d.get("tool_response")
    failed = H.is_failure(d)
    brief = request_brief(ti)
    line = tool_line(name, brief, failed)
    if failed:
        line += R.DIM + " ✗" + R.RST                 # the file one-liner's mark
    # Click-to-view: stash the request + the answer under the call's
    # tool_use_id and wrap the line in the claude-copy:///…/view hyperlink, the
    # same protocol a Read one-liner expands with (core.copy.stash). A call with
    # no id (none observed, but the field is Claude's) keeps a plain line.
    vid = None
    gid = d.get("tool_use_id") or None
    if gid:
        try:
            vops = view_block(name, brief, ti, tr, failed)
        except Exception:
            vops = None
            A.error(LOG, "view-stash (tool)", {"tool": name, "gid": gid})
        line, vid = C.stash(LOG, gid, vops, line, {"tool": name, "kind": "tool"})
    O.emit(LOG, O.line(line, view=vid, act=O.ACT_TOOL))
    # The scoreboard's tools breakdown, keyed by the raw tool name exactly as the
    # file and command formatters key theirs (no file, no line deltas — a tool
    # call touches neither).
    O.bump(LOG, tool=name)
    A.hook_event(d, decision="rendered: %s(%s)%s%s"
                 % (name, brief, " FAILED" if failed else "",
                    " +view" if vid else ""))


def entry():
    H.run(main)
