# plugins/claude_code/hookkit.py — the shared harness for the short-lived hook
# handlers (historical name: claude_hook.py — the compat shim re-exports this)
# (claude-cmd-pre / claude-cmd-fmt / claude-file-fmt / claude-subagent-fmt /
# claude-monitor-fmt / claude-task-fmt).
#
# Every handler has the same skeleton: parse the JSON payload from stdin, derive
# the mirror log, decide (auditing the ignored paths too), maybe spawn a detached
# streamer, and swallow ANY exception at top level — recording it to the audit
# first (the "hooks must never block or fail" invariant in CLAUDE.md). Before
# this module the skeleton was copy-pasted six times, near-verbatim, and the
# detached-spawn scaffolding four times; centralizing it makes the invariants
# (audit-before-swallow, start_new_session=True) single-sited.
#
# NOTE the agent_id main-session guard is deliberately NOT part of read_payload:
# most handlers skip agent_id events (the substream owns subagent rendering) but
# claude-monitor-fmt intentionally renders subagent monitors. Each handler makes
# that call explicitly with ignore().
import json
import os
import subprocess
import sys

try:
    from core import audit as A         # always-on audit trail (CLAUDE_AUDIT=0 disables)
except Exception:                       # audit must never break a hook
    class _NoAudit:
        def __getattr__(self, _):
            return lambda *a, **k: None
    A = _NoAudit()
from core import paths as P

# The repo root, where the entry scripts live (this file is two package levels
# below it) — script() must resolve SIBLING ENTRY SCRIPTS, not package modules.
HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_LOG = ""  # last mirror log seen by read_payload(); run()'s crash audit uses it

# When the single per-event dispatcher (claude-hook.py -> plugins.claude_code.
# dispatch) runs a formatter in-process, stdin has ALREADY been consumed by the
# dispatcher (a stream can only be read once). It injects the parsed payload here
# so read_payload() returns it instead of re-reading an empty stdin. None = the
# normal standalone-shim path (read stdin). See dispatch.py.
_INJECTED = None


def set_payload(d):
    global _INJECTED
    _INJECTED = d


def clear_payload():
    global _INJECTED
    _INJECTED = None


def log_path(d):
    """The mirror log for a hook payload, keyed by session_id so PARALLEL Claude
    sessions get separate logs (separate content). Falls back to a cwd slug if a
    payload somehow lacks session_id. claude-split.py derives the SAME path (from
    the SessionStart payload's session_id, and from the focused pane's
    claude_session var) so the renderer tails exactly what the producers write."""
    return P.mirror_log(d.get("session_id"), d.get("cwd"))


def script(name):
    """Absolute path of a sibling script (a streamer / the tab dispatcher)."""
    return os.path.join(HERE, name)


def read_payload():
    """Parse the hook payload from stdin and derive its mirror log.
    Returns (payload, log), or (None, "") after auditing a malformed payload —
    callers just `if d is None: return`."""
    global _LOG
    if _INJECTED is not None:
        d = _INJECTED                       # dispatcher pre-read stdin for us
    else:
        try:
            d = json.load(sys.stdin)
        except Exception:
            A.error("", "payload parse (stdin not valid JSON)")
            return None, ""
    _LOG = log_path(d)
    return d, _LOG


def ignore(d, reason):
    """Audit an early-return decision (the decision column is what makes
    hook_events diagnostic). Returns None so handlers can `return ignore(...)`."""
    A.hook_event(d, decision="ignored: " + reason)
    return None


def is_failure(d):
    """True when this payload arrived on the failure event — failures fire
    PostToolUseFailure, NOT PostToolUse (see CLAUDE.md invariants)."""
    return "Failure" in (d.get("hook_event_name") or "")


def spawn_streamer(name, argv, log, env=None, purpose="", audit_argv=None):
    """Spawn a sibling script detached, audited. start_new_session=True is
    load-bearing: a plain child would sit in the hook's process group, which
    Claude Code waits to drain (this hung SessionStart once — see
    claude-codex-launch.py). Returns the Popen, or None (missing script or spawn
    failure, audited) — the CALLER rolls back its own slot/marker state."""
    path = script(name)
    if not os.path.exists(path):
        # Audited (the docstring promised it, the code didn't): a renamed/deleted
        # sibling script otherwise means every block silently never streams, with
        # no spawn row and no errors row to triage from.
        A.error(log, "spawn " + name + " (script missing)", {"path": path})
        return None
    try:
        proc = subprocess.Popen(
            [sys.executable, path] + [str(a) for a in argv],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True, env=env)
    except Exception:
        A.error(log, "spawn " + name, {"argv": [str(a) for a in argv]})
        return None
    A.spawn(log, proc.pid, [path] + list(audit_argv if audit_argv is not None else argv),
            purpose=purpose)
    return proc


def stream_env(src=None, done=None, cmd=None, group=None, own=False,
               skip_existing=False):
    """The ONE builder of claude-stream.py's env contract (CLAUDE_STREAM_*).
    Every tailer launch site — main-session fg (cmd_pre), bg (cmd_fmt), a
    subagent's fg (substream.spawn_fg_tailer) — goes through here, so a new key
    reaches all of them at once; assembling the env by hand per launch site is
    how the subagent fg path silently missed the content-render key and a
    subagent's `cat foo.kt` streamed uncoloured. `cmd` is the ORIGINAL
    (pre-tee-wrap) command: the tailer derives its own content-render mode
    (md/json/yaml/code) from it — launchers pass the command, never the
    decision. `skip_existing` tails the file from its size at spawn (a `>>`
    append target / a Ctrl+B hand-off — the prior bytes are not this job's)."""
    env = dict(os.environ)
    for k, v in (("CLAUDE_STREAM_SRC", src), ("CLAUDE_STREAM_DONE", done),
                 ("CLAUDE_STREAM_CMD", cmd), ("CLAUDE_STREAM_GROUP", group)):
        if v:
            env[k] = v
    if own:
        env["CLAUDE_STREAM_OWN"] = "1"
    if skip_existing:
        env["CLAUDE_STREAM_SKIP_EXISTING"] = "1"
    return env


def notify_tab(dispatch, args, log):
    """Fire the tab-status dispatcher synchronously, best-effort (it exits fast;
    failures must never break the calling hook)."""
    try:
        subprocess.run([script("claude-tab-status.py"), dispatch] + list(args),
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=10)
    except Exception:
        # A dropped dispatch is a stuck-tab-colour symptom with, before this,
        # no errors row AND no transitions row (the dispatcher never ran).
        A.error(log, "notify_tab " + dispatch)


def run(main, **context):
    """Top-level entry for a hook handler: run it, swallow anything it leaks —
    auditing first, against the last payload's mirror log."""
    try:
        main()
    except Exception:
        A.error(_LOG, "main", context or None)
