# plugins/claude_code/ask_fmt.py — the AskUserQuestion pending-state tracker.
# Entry point: claude-ask-fmt.py (a thin shim — the entry FILENAME is the audit
# vocabulary); in-process via dispatch.py on PreToolUse/PostToolUse(+Failure)
# matcher AskUserQuestion, plus the turn-boundary clears (Stop/StopFailure,
# UserPromptSubmit).
#
# WHY: the web dashboard's ask card (docs/dashboard.md, *Web ask*). While the
# TUI's question dialog is up, the ONLY machine-readable trace is the
# PreToolUse payload — the dialog itself is pixels. This handler stashes the
# pending ask (questions + tool_use_id) in the state DB kv `ask-pending`, the
# session SSE surfaces it to the page, and the /answer endpoint drives the
# real dialog (dashboard/askdialog.py) with the stash as its map.
#
# Clearing has NO closing hook in the decline paths (measured 2026-07-18):
# a real answer fires PostToolUse, but Esc / "Chat about this" / an
# empty-"Type something" Enter all resolve the tool as "User declined to
# answer questions" with NO PostToolUse(Failure) at all. The dialog also
# cannot outlive its turn. So the stash clears on: PostToolUse(+Failure) of
# the tool (answered), Stop/StopFailure (turn ended — covers every decline),
# and UserPromptSubmit (a new turn started). A stale card is additionally
# harmless because the /answer endpoint verifies the dialog on SCREEN before
# pressing anything.
from core import state as ST
from plugins.claude_code import hookkit as H

A = H.A

KEY = "ask-pending"


def main():
    d, LOG = H.read_payload()
    if d is None:
        return
    ev = d.get("hook_event_name") or ""
    if d.get("agent_id"):
        # a subagent/teammate inner ask never paints the MAIN session's dialog
        return H.ignore(d, "subagent event (agent_id present)")
    if ST.parked(LOG):
        # no live state DB = an unhosted session (headless claude -p / daemon)
        # or one already parked — NOTHING here may connect: the DB's
        # file-existence is the session-alive signal watchers poll, and kv_get
        # would CREATE it (the ghost-DB bug class). No pane ⇒ no web card
        # either, so there is nothing to stash. Only PreToolUse is audited
        # (Stop/UserPromptSubmit fire for every headless turn — noise).
        if ev == "PreToolUse":
            H.ignore(d, "no state DB (unhosted session)")
        return
    sdb = ST.db_path(LOG)
    if ev == "PreToolUse":
        ti = d.get("tool_input") or {}
        questions = ti.get("questions") or []
        if not isinstance(questions, list) or not questions:
            return H.ignore(d, "no questions in tool_input")
        pending = {"tool_use_id": d.get("tool_use_id") or "",
                   "questions": questions}
        ST.kv_set(LOG, KEY, pending)
        A.state_file(LOG, sdb, KEY,
                     {"action": "write", "tool_use_id": pending["tool_use_id"],
                      "questions": len(questions)})
        A.hook_event(d, decision="ask-pending stashed (%d question%s)"
                     % (len(questions), "" if len(questions) == 1 else "s"))
        return
    # every other routed event is a CLEAR signal; only audit when there was
    # something to clear (Stop fires every turn — an empty clear is noise)
    if ST.kv_get(LOG, KEY) is None:
        return
    reason = {"PostToolUse": "answered",
              "PostToolUseFailure": "failed",
              "Stop": "turn ended", "StopFailure": "turn ended",
              "UserPromptSubmit": "new prompt"}.get(ev, ev or "unknown")
    ST.kv_del(LOG, KEY)
    A.state_file(LOG, sdb, KEY, {"action": "remove", "reason": reason})
    A.hook_event(d, decision="ask-pending cleared (%s)" % reason)


def entry():
    H.run(main)
