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
#
# THE KEY DRIFTS (measured 2026-07-30, session 6e58ae19): after a `--resume`
# Claude Code stops keying the dir by the session id — the resumed process
# mints a fresh internal list id (`session-275b8fdf`, matching no sid, no
# subagent, no prompt_id) and writes THERE, so the sid-keyed snapshot froze on
# the pre-resume list (the dashboard card showed a dead 9-task list while the
# TUI worked a new one). So the dir is RESOLVED, not assumed: each task event
# names a task (task_id/subject), and the dir that actually holds THAT task is
# the one Claude Code is writing — sid dir first, else a bounded newest-mtime
# scan of the sibling session-* dirs; a scan hit is PINNED in the `tasks-dir`
# kv (audited) so the snapshots that carry no probe (a bare status flip) stay
# on the resolved dir. A matching sid dir always wins over a stale pin, so a
# genuinely fresh session self-corrects without an un-pin gesture.
import json
import os

from core import ops as O
from core import state as ST
from plugins.claude_code import hookkit as H
from plugins.claude_code import model as M

A = O.A    # audit trail (real module, or a no-op stub if it failed to import)

CREATED_RGB = O.AMBER   # a task entering the list
DONE_RGB    = O.GREEN   # a task finished

# The task line's opening GLYPHS — this module's vocabulary, and named because
# the web mirror's classifier reads them back (dashboard/opshtml/actclass.py, the
# `task` activity class): the glyph is what says "this row is a task", and a
# second spelling of it there would drift the moment either changes.
GLYPH_NEW  = "✚"        # a task created
GLYPH_DONE = "✓"        # a task completed
GLYPHS     = (GLYPH_NEW, GLYPH_DONE)

KEY = "tasks"          # the state-DB kv stash the dashboard's tasks card reads
PIN_KEY = "tasks-dir"  # kv: the drift-resolved task dir (see the header note)
SCAN_MAX = 40          # newest sibling session-* dirs probed on a drift scan


def tasks_dir(sid):
    """The session's DEFAULT on-disk task-list dir. Claude Code keys it by the
    FIRST uuid segment of the session id under the active config root
    ($CLAUDE_CONFIG_DIR when the subscription switcher pins one, ~/.claude
    otherwise) — until a resume drifts the key (header note; resolve_dir)."""
    root = M.config_dir()
    return os.path.join(root, "tasks", "session-" + sid.split("-")[0])


def _probe(d):
    """(task_id, subject) the event names — the match key for finding which
    on-disk dir Claude Code is REALLY writing. TaskCreated/TaskCompleted carry
    task_id + task_subject; a PostToolUse(TaskUpdate) carries tool_input.taskId,
    a PostToolUse(TaskCreate) only tool_input.subject. ("", "") = no probe."""
    if (d.get("hook_event_name") or "") in ("PostToolUse", "PostToolUseFailure"):
        ti = d.get("tool_input") or {}
        return str(ti.get("taskId") or ""), ti.get("subject") or ""
    return str(d.get("task_id") or ""), d.get("task_subject") or ""


def _dir_matches(path, tid, subj):
    """Does this task dir hold the probed task? An id probe demands
    <tid>.json (subject agreeing when both are known); a subject-only probe
    (TaskCreate's Post — no id yet) matches any record with that subject."""
    try:
        if tid:
            with open(os.path.join(path, tid + ".json"), encoding="utf-8") as f:
                rec = json.load(f)
            return not subj or (rec.get("subject") or "") == subj
        if subj:
            for name in os.listdir(path):
                if not name.endswith(".json"):
                    continue
                with open(os.path.join(path, name), encoding="utf-8") as f:
                    if (json.load(f).get("subject") or "") == subj:
                        return True
    except (OSError, ValueError):
        return False
    return False


def resolve_dir(d, sid, LOG):
    """The task dir Claude Code is actually writing for this session: the
    sid-keyed default when it holds the event's task, else the pinned
    drift dir, else a newest-mtime scan of the sibling session-* dirs —
    a scan hit is pinned (kv + audit) for the probe-less snapshots."""
    default = tasks_dir(sid)
    pin = ST.kv_get(LOG, PIN_KEY) or {}
    pinned = pin.get("dir") or ""
    tid, subj = _probe(d)
    if not tid and not subj:                  # no probe — trust what we have
        return pinned if pinned and os.path.isdir(pinned) else default
    if _dir_matches(default, tid, subj):      # sid dir wins over a stale pin
        return default
    if pinned and _dir_matches(pinned, tid, subj):
        return pinned
    root = os.path.dirname(default)
    cands = []
    try:
        for e in os.scandir(root):
            try:
                if e.name.startswith("session-") and e.is_dir():
                    cands.append((e.stat().st_mtime, e.path))
            except OSError:
                continue
    except OSError:
        cands = []
    cands.sort(reverse=True)
    for _mt, path in cands[:SCAN_MAX]:
        if path in (default, pinned):
            continue
        if _dir_matches(path, tid, subj):
            ST.kv_set(LOG, PIN_KEY, {"dir": path})
            A.state_file(LOG, ST.db_path(LOG), PIN_KEY,
                         {"action": "pin", "dir": path, "sid_dir": default,
                          "task_id": tid, "subject": subj[:80]})
            return path
    return pinned if pinned and os.path.isdir(pinned) else default


def read_tasks(d, sid, LOG):
    """All task records in the session's resolved task dir, sorted numerically
    by id. Unreadable/malformed files are skipped (the writer holds a .lock we
    don't take — a torn read self-heals on the next snapshot)."""
    path = resolve_dir(d, sid, LOG)
    out = []
    try:
        names = os.listdir(path)
    except OSError:
        return out
    for name in names:
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(path, name), encoding="utf-8") as f:
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
    tasks = read_tasks(d, sid, LOG)
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
        glyph, rgb = GLYPH_DONE, DONE_RGB
    else:                                    # TaskCreated (or anything task-ish)
        glyph, rgb = GLYPH_NEW, CREATED_RGB
    text = f"{glyph} task #{tid} · {subj}" if subj else f"{glyph} task #{tid}"
    O.emit(LOG, O.blank(), O.label(text, rgb))
    A.hook_event(d, decision=f"rendered: {text}; {snapshot(d, LOG)}")


def entry():
    H.run(main)
