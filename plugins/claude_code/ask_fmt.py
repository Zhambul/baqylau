# plugins/claude_code/ask_fmt.py — the pending MODAL-DIALOG tracker: the
# AskUserQuestion question dialog AND the ExitPlanMode plan-approval dialog.
# Entry point: claude-ask-fmt.py (a thin shim — the entry FILENAME is the audit
# vocabulary; it predates the plan half, so it keeps its name); in-process via
# dispatch.py on PreToolUse/PostToolUse(+Failure) matcher
# AskUserQuestion|ExitPlanMode, plus the turn-boundary clears (Stop/
# StopFailure, UserPromptSubmit).
#
# WHY: the web dashboard's ask + plan cards (docs/dashboard.md, *Web ask* /
# *Web plan mode*). While a TUI dialog is up, the ONLY machine-readable trace
# is the PreToolUse payload — the dialog itself is pixels. This handler
# stashes the pending dialog in the state DB kv (`ask-pending`: questions +
# tool_use_id; `plan-pending`: plan markdown + planFilePath + tool_use_id),
# the session SSE surfaces it to the page, and the /answer / /plan-decision
# endpoints drive the real dialog (dashboard/askdialog.py, plandialog.py)
# with the stash as their map.
#
# Clearing has NO closing hook in the decline paths (measured 2026-07-18):
# a real answer/approval fires PostToolUse, but every decline — ask: Esc /
# "Chat about this" / an empty-"Type something" Enter; plan: Esc / a typed
# "Tell Claude what to change" feedback — fires NOTHING (the transcript gains
# a rejection tool_result only). A dialog also cannot outlive its turn. So
# each stash clears on: PostToolUse(+Failure) of ITS tool, Stop/StopFailure
# (turn ended), and UserPromptSubmit (a new turn) — the boundaries clear BOTH
# keys. A stale card is additionally harmless because the endpoints verify
# the dialog on SCREEN before pressing anything (and self-heal the stash on
# an `open` bail).
from core import state as ST
from plugins.claude_code import hookkit as H

A = H.A

KEY = "ask-pending"
PLAN_KEY = "plan-pending"
# tool → its kv key; also the PostToolUse(+Failure)-clear scoping
KEYS = {"AskUserQuestion": KEY, "ExitPlanMode": PLAN_KEY}


def _stash(d, LOG):
    """The PreToolUse write — returns the pending record for the payload's
    tool, or None (malformed input, audited as ignored)."""
    ti = d.get("tool_input") or {}
    tool = d.get("tool_name") or ""
    if tool == "AskUserQuestion":
        questions = ti.get("questions") or []
        if not isinstance(questions, list) or not questions:
            H.ignore(d, "no questions in tool_input")
            return None, ""
        return ({"tool_use_id": d.get("tool_use_id") or "",
                 "questions": questions}, "%d question%s" % (
                     len(questions), "" if len(questions) == 1 else "s"))
    plan = ti.get("plan") or ""
    if not isinstance(plan, str) or not plan.strip():
        H.ignore(d, "no plan in tool_input")
        return None, ""
    return ({"tool_use_id": d.get("tool_use_id") or "", "plan": plan,
             "planFilePath": ti.get("planFilePath") or ""},
            "%d-char plan" % len(plan))


def main():
    d, LOG = H.read_payload()
    if d is None:
        return
    ev = d.get("hook_event_name") or ""
    tool = d.get("tool_name") or ""
    if d.get("agent_id"):
        # a subagent/teammate inner dialog never paints the MAIN session's
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
        pending, what = _stash(d, LOG)
        if pending is None:
            return
        key = KEYS[tool]
        ST.kv_set(LOG, key, pending)
        A.state_file(LOG, sdb, key,
                     {"action": "write",
                      "tool_use_id": pending["tool_use_id"], "what": what})
        A.hook_event(d, decision="%s stashed (%s)" % (key, what))
        return
    # every other routed event is a CLEAR signal: the tool's own
    # PostToolUse(+Failure) clears ITS key; the turn boundaries clear both.
    # Only audit when there was something to clear (Stop fires every turn —
    # an empty clear is noise).
    keys = [KEYS[tool]] if tool in KEYS else list(KEYS.values())
    reason = {"PostToolUse": "answered",
              "PostToolUseFailure": "failed",
              "Stop": "turn ended", "StopFailure": "turn ended",
              "UserPromptSubmit": "new prompt"}.get(ev, ev or "unknown")
    for key in keys:
        if ST.kv_get(LOG, key) is None:
            continue
        ST.kv_del(LOG, key)
        A.state_file(LOG, sdb, key, {"action": "remove", "reason": reason})
        A.hook_event(d, decision="%s cleared (%s)" % (key, reason))


def entry():
    H.run(main)
