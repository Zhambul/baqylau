# core/spawn.py — THE detached audited process spawn.
#
# Single charter: the one owner of the detach mechanics every long-lived child
# needs — subprocess.Popen with all stdio to DEVNULL and start_new_session=True
# — plus the audit rows around it (A.spawn on success, A.error on a missing
# script or a spawn failure; it never raises into the caller).
#
# start_new_session=True is LOAD-BEARING: a plain child sits in the hook's
# process group, which Claude Code waits to drain — a never-exiting watcher
# launched with bash `&` hung SessionStart once (see plugins/codex/launch.py).
#
# Extracted from three byte-similar copies of the same pattern:
# plugins/claude_code/hookkit.spawn_streamer (which stays as the plugin-facing
# wrapper that resolves a bin/ sibling NAME to its path), plugins/codex/
# launch.py, and plugins/codex/watch.spawn. Tool-agnostic by construction: it
# takes an absolute script path and knows nothing about hook payloads or
# rollouts.
import os
import subprocess
import sys

from core.noaudit import load_audit

A = load_audit()   # always-on audit trail (CLAUDE_AUDIT=0 disables); inert stub if it can't import


def spawn_detached(path, argv, log, env=None, purpose="", audit_argv=None):
    """Spawn the script at absolute `path` detached, audited. Returns the
    Popen, or None (missing script or spawn failure, audited) — the CALLER
    rolls back its own slot/marker state. A missing script is audited too: a
    renamed/deleted sibling otherwise means every block silently never
    streams, with no spawn row and no errors row to triage from. `audit_argv`
    overrides what the spawns row records (a launcher may drop bulky args)."""
    name = os.path.basename(path)
    if not os.path.exists(path):
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
