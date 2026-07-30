# plugins/claude_code/skill_fmt.py — the SKILL invocation one-liner.
# Entry point: claude-skill-fmt.py (a thin shim — the entry FILENAME is the audit vocabulary).
# claude-skill-fmt.py MIRROR_LOG WIDTH
#
# Renders one Skill tool call into the mirror as a compact block: a header naming
# the skill plus, behind the click, the ARGS it was invoked with.
#
#   terminal:  ✦ skill · slack        (violet)   +  the args behind its gutter
#   web:       ⏺ Skill(slack)                    +  the args behind the click
#
# The web wording is Claude Code's own (`core/streamfmt.skill_note` — its transcript
# prints `⏺ Skill(slack)`), which is what was asked for: *"I want skills in default
# mode to appear like this ⏺ Skill(slack), and in focus mode in the summary to appear,
# and in both places it is expandable"*. So the block is a NOTE block (core/ops.py's
# `note`), exactly like an agent's or a message's: one quiet line in default, folded
# into the run summary in focus (`skill` is in VIEW_FOLD.focus only), and clickable in
# both — the fold reveals this line, and the line reveals the args.
#
# Wired on PostToolUse AND PostToolUseFailure (the invariant: a failure arrives only on
# the latter). Both hooks were measured to fire for this tool — 294 PreToolUse + 294
# PostToolUse rows with tool_name=Skill in the audit — with the payload shape
#
#   tool_input    {"skill": "slack", "args": "read https://…"}
#   tool_response {"success": true, "commandName": "slack", "allowedTools": ["Bash"]}
#
# i.e. the response carries NO skill body: Claude Code injects the loaded SKILL.md into
# the conversation as a user-shaped turn (transcript `isMeta` — the web mirror's
# `data-injected`), it is not a tool result. The args are therefore the only content
# there is to put behind the click, and a skill invoked without args gets a line with
# nothing behind it (the note block's own empty-body guard makes it unclickable).
from core import ops as O
from core import state as ST
from core import streamfmt as SF
from plugins.claude_code import hookkit as H

A = O.A    # audit trail (real module, or a no-op stub if it failed to import)

SKILL_RGB = O.VIOLET   # a skill invocation (the semantic table's own hue for it)
FAIL_RGB  = O.RED      # …and a failed one, the shared failure colour

# The args cap. A skill's args are a REQUEST, not output — one line to a short
# paragraph in practice (the longest measured is a 130-char log query) — so this is a
# backstop against a pathological paste, not a display policy. A touch above a
# subagent's tool-request excerpt (substream_render.CAP_TOOL_REQ, 10), which is the same
# kind of thing shown in a denser place.
CAP_ARGS = 12


def main():
    d, LOG = H.read_payload()
    if d is None:
        return
    if (d.get("tool_name") or "") != "Skill":
        return H.ignore(d, "not the Skill tool")
    if d.get("agent_id"):
        # a subagent's own skill call — the substream owns that stream's rendering
        # (its generic tool renderer paints it), and handling both duplicates the row
        return H.ignore(d, "subagent event (agent_id present)")
    if ST.parked(LOG):
        # no live state DB = unhosted (headless/daemon) or already parked — emitting
        # would CREATE the DB whose file-existence is the session-alive signal
        return H.ignore(d, "no state DB (unhosted session)")
    ti = d.get("tool_input") or {}
    tr = d.get("tool_response") or {}
    # the skill's NAME: the input's own field, with the response's `commandName` as the
    # fallback (they agree in every measured row; the input is the request, so it wins)
    name = str(ti.get("skill") or (tr.get("commandName") if isinstance(tr, dict) else "")
               or "?").strip()
    failed = H.is_failure(d) or (isinstance(tr, dict) and tr.get("success") is False)
    args = SF.cap(str(ti.get("args") or "").strip(), CAP_ARGS)
    rgb = FAIL_RGB if failed else SKILL_RGB
    g = O.new_group(LOG) if args else None
    ops = [O.label("%s skill · %s" % (SF.SKILL_MARK, name), rgb, g=g,
                   act=O.ACT_SKILL, note=SF.skill_note(name, failed))]
    if args:
        ops.append(O.gut(args, rgb, g=g))
    O.emit(LOG, *ops)
    A.hook_event(d, decision="rendered: skill %s%s (%d chars of args)"
                 % (name, " FAILED" if failed else "", len(args)))


def entry():
    H.run(main)
