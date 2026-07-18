# plugins/claude_code/task_fmt.py — the task-list tracker: the TaskCreated/
# TaskCompleted mirror one-liner AND the `tasks` kv snapshot behind the web
# dashboard's pinned tasks card (docs/dashboard.md, *Web tasks*).
# Entry point: claude-task-fmt.py (a thin shim — the entry FILENAME is the audit vocabulary).
# claude-task-fmt.py MIRROR_LOG WIDTH
#
# Renders a shared-task-list event into the command mirror as one compact line.
# Driven by the TaskCreated / TaskCompleted hooks (which fire in the lead session
# when the session/team creates / completes a task). The event name is read
# from the hook payload (hook_event_name), so no phase argument is needed.
#
#   TaskCreated   -> "✚ task #<id> · <subject>"   (amber)
#   TaskCompleted -> "✓ task #<id> · <subject>"   (green)
#
# Empirically the payload carries task_id + task_subject + task_description (NOT the
# "task_title"/"task_status" the docs mention). Task STATE, however, does live on
# disk (measured 2026-07-18): `<CLAUDE_CONFIG_DIR|~/.claude>/tasks/session-<first
# uuid segment of sid>/<id>.json`, one `{id, subject, description, activeForm,
# status, blocks, blockedBy}` per task — but Claude Code DELETES the whole dir's
# contents at session end, so every past session's dir reads empty. Hence the kv
# snapshot: on every task-touching hook (TaskCreated/TaskCompleted, plus
# PostToolUse(+Failure) of TaskCreate|TaskUpdate — the ONLY signal for a
# pending→in_progress claim, which fires no dedicated hook) this handler re-reads
# the dir and stashes the full list as the `tasks` kv in the state DB, which
# survives park. The dir at op time is authoritative; there is deliberately no
# clear-on-empty guard — an empty read right after a task op means the list IS
# empty (all tasks deleted), not that cleanup ran (no hook fires at cleanup).
import json
import os

from core import ops as O
from core import state as ST
from plugins.claude_code import hookkit as H

A = O.A    # audit trail (real module, or a no-op stub if it failed to import)

CREATED_RGB = O.AMBER   # a task entering the list
DONE_RGB    = O.GREEN   # a task finished

KEY = "tasks"          # the state-DB kv stash the dashboard's tasks card reads


def tasks_dir(sid):
    """The session's on-disk task-list dir. Claude Code keys it by the FIRST
    uuid segment of the session id under the active config root ($CLAUDE_CONFIG_DIR
    when the subscription switcher pins one, ~/.claude otherwise)."""
    root = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
    return os.path.join(root, "tasks", "session-" + sid.split("-")[0])


def read_tasks(sid):
    """All task records in the session's task dir, sorted numerically by id.
    Unreadable/malformed files are skipped (the writer holds a .lock we don't
    take — a torn read self-heals on the next snapshot)."""
    d = tasks_dir(sid)
    out = []
    try:
        names = os.listdir(d)
    except OSError:
        return out
    for name in names:
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(d, name)) as f:
                rec = json.load(f)
            if isinstance(rec, dict):
                out.append(rec)
        except (OSError, ValueError):
            continue
    def _id(rec):
        try:
            return (0, int(rec.get("id") or 0))
        except (TypeError, ValueError):
            return (1, 0)
    out.sort(key=_id)
    return out


def snapshot(d, LOG):
    """Re-read the task dir and stash the list as the `tasks` kv (the web
    dashboard's pinned-card source). Returns the audit decision fragment."""
    sid = d.get("session_id") or ""
    if not sid:
        return "no session_id, snapshot skipped"
    tasks = read_tasks(sid)
    ST.kv_set(LOG, KEY, {"tasks": tasks})
    counts = {}
    for t in tasks:
        st = t.get("status") or "?"
        counts[st] = counts.get(st, 0) + 1
    what = " ".join(f"{k}:{v}" for k, v in sorted(counts.items())) or "empty"
    A.state_file(LOG, ST.db_path(LOG), KEY,
                 {"action": "write", "tasks": len(tasks), "what": what})
    return f"{KEY} stashed ({len(tasks)}: {what})"


def main():
    d, LOG = H.read_payload()
    if d is None:
        return
    if d.get("agent_id"):
        # a subagent/teammate inner call — the lead session's own TaskCreated/
        # TaskCompleted events cover the shared list (main-session-only invariant)
        return H.ignore(d, "subagent event (agent_id present)")
    if ST.parked(LOG):
        # no live state DB = unhosted (headless/daemon) or already parked —
        # kv_set would CREATE the DB whose file-existence is the alive signal
        return H.ignore(d, "no state DB (unhosted session)")
    ev = d.get("hook_event_name") or ""
    if ev in ("PostToolUse", "PostToolUseFailure"):
        # TaskCreate|TaskUpdate outcome — snapshot only, no mirror line (creation
        # and completion already paint via their dedicated events below)
        A.hook_event(d, decision=snapshot(d, LOG))
        return
    tid  = d.get("task_id") or "?"
    # task_subject with a task_description fallback — the payload carries these two
    # (NOT the "task_title" the docs mention; a speculative fallback on it was dropped).
    subj = d.get("task_subject") or d.get("task_description") or ""
    if ev == "TaskCompleted":
        glyph, rgb = "✓", DONE_RGB
    else:                                    # TaskCreated (or anything task-ish)
        glyph, rgb = "✚", CREATED_RGB
    text = f"{glyph} task #{tid} · {subj}" if subj else f"{glyph} task #{tid}"
    O.emit(LOG, O.blank(), O.label(text, rgb))
    A.hook_event(d, decision=f"rendered: {text}; {snapshot(d, LOG)}")


def entry():
    H.run(main)
