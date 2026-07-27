# plugins/claude_code/mail_fmt.py — the team-mail MESSAGE row: one mirror block per
# message SENT, carrying its text.
# Entry point: claude-mail-fmt.py (a thin shim — the entry FILENAME is the audit vocabulary).
#
# Driven by SendMessage's PostToolUse(+Failure). Why a hook at all, when msgs.py
# already tracks team mail: the tracker POLLS the inboxes once a second, so it only
# ever sees a message still sitting unread at a tick. Measured on one reviewed lead
# session: 33 messages sent, 12 arrivals recorded, and 10 of those 12 were lifecycle
# frames — so 2 of 33 real messages left a row, and the other 31 were consumed
# between ticks and vanished. No amount of work on the poller's rows can show a
# message it never saw. The hook, by contrast, fires on every send, at send time,
# with the full text in `tool_input.message` (docs/dashboard.md, *Team mail*).
#
# So the two sources split by role: this handler writes the MESSAGE (`✉ <from> →
# <to>` + its text), the poller writes the DELIVERY/READ plumbing lines. Both stamp
# the same `msg_id` as the op's `mid`, so the web counts one message however many
# rows speak about it.
#
# MAIN-SESSION-ONLY DOES NOT APPLY HERE, deliberately (the invariant in CLAUDE.md,
# and the same exception cmd_pre.py takes for a subagent's teed command): a
# teammate's SendMessage fires with an `agent_id`, and a teammate's outgoing mail is
# the largest part of team mail — skipping agent events would drop nearly all of it.
# The op is emitted UNSTAMPED (no ops `src`), which is what puts it in the main
# mirror where the lead reads it, and the payload's session_id is the lead's, so the
# state DB it writes to is the main session's either way.
#
# Empirically (2.1.220, measured 2026-07-27) the payload carries:
#   tool_input     {to, summary, message}
#   tool_response  {success, message, msg_id, routing:{sender, target, summary, …}}
#   agent_id/agent_type   present when a TEAMMATE sent it (absent for the lead)
# `tool_input.to` is what the sender typed and can differ from the recipient's inbox
# name ("main" vs "team-lead"), which is why the poller's rows and these cannot be
# joined by recipient — they are joined by `msg_id`.
from core import ops as O
from core import state as ST
from plugins.claude_code import hookkit as H
from plugins.claude_code import msgs as MSGS

A = O.A    # audit trail (real module, or a no-op stub if it failed to import)


def sender(d):
    """Who sent this message. `routing.sender` is Claude Code's own answer and is
    trusted first; `agent_type` is the teammate's name when routing is absent; a
    payload with neither is the LEAD's own send, which has no name of its own in the
    payload at all — the team's own name for it is the inbox it reads."""
    tr = d.get("tool_response") or {}
    routing = tr.get("routing") or {}
    return (routing.get("sender") or d.get("agent_type") or "main")


def main():
    d, LOG = H.read_payload()
    if d is None:
        return
    if ST.parked(LOG):
        # no live state DB = unhosted (headless/daemon) or already parked — emitting
        # would CREATE the DB whose file-existence is the session-alive signal
        return H.ignore(d, "no state DB (unhosted session)")
    ti = d.get("tool_input") or {}
    tr = d.get("tool_response") or {}
    if tr.get("success") is False or d.get("hook_event_name") == "PostToolUseFailure":
        # a refused send delivered nothing: the mirror would show a message that was
        # never passed. The audit row below is the trace that it was tried.
        return H.ignore(d, "send failed: %s" % (tr.get("message") or "?"))
    text = ti.get("message")
    if not isinstance(text, str):
        # the structured-content form ({message: {type: …}}) is a protocol frame, not
        # prose — the poller's lifecycle line is the right surface for those
        return H.ignore(d, "not a plain-text message (%s)" % type(text).__name__)
    frm = sender(d)
    to = ti.get("to") or "?"
    summ = ti.get("summary") or ""
    mid = tr.get("msg_id") or ""
    ops = MSGS.sent_ops(frm, to, summ, text, mid, LOG)
    O.emit(LOG, *ops)
    A.hook_event(d, decision="rendered: %s → %s (%d chars, msg_id %s)"
                 % (frm, to, len(text), mid or "-"))


def entry():
    H.run(main)
