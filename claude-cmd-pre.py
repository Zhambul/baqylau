#!/usr/bin/env python3
# claude-cmd-pre.py — PreToolUse(Bash) hook: makes long foreground commands stream
# live in the mirror, the same way background commands already do.
#
# Claude Code only hands a foreground command's output to a hook at PostToolUse,
# once it has already finished (see claude-cmd-fmt.py) — there's no other way to
# see it while it's still running. To fix that we rewrite the command here (via
# PreToolUse's `updatedInput`) so it ALSO tees its stdout/stderr into a side file,
# emit the "▶ foreground" header immediately, and spawn a detached claude-stream.py
# to tail that file — the exact mechanism background commands already use, just
# triggered a hook earlier. claude-cmd-fmt.py's PostToolUse handler checks the
# "fg-live" state record this leaves behind and, if present, hands the finish chip off
# to the tailer (via a ".done" sentinel next to the tee file) instead of rendering
# the whole block itself.
#
# Only top-level (non-subagent, non-background) commands are wrapped: a subagent
# can run several foreground Bash calls in parallel, and this session's single
# "fg-live" record only tracks ONE in-flight command at a time. That's safe for
# the main loop, which always awaits one Bash call's Pre->exec->Post cycle before
# starting the next, but not for concurrent subagents — those keep the old bundled
# rendering (already excluded below via the agent_id check, same as claude-cmd-fmt.py).
import json, os, shlex, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import claude_hook as H
import claude_ops as O
import claude_slots
import claude_state as S

A = O.A    # audit trail (real module, or a no-op stub if it failed to import)

LBL_FG = (170, 185, 210)  # slate — same colour claude-cmd-fmt.py uses for an OK foreground block


def main():
    d, log = H.read_payload()
    if d is None:
        return
    if os.environ.get("CLAUDE_MIRROR_LIVE_FG", "1") == "0":
        return H.ignore(d, "CLAUDE_MIRROR_LIVE_FG=0")   # escape hatch if the rewrite ever misbehaves
    if d.get("agent_id"):
        return H.ignore(d, "agent_id")
    ti = d.get("tool_input") or {}
    cmd = ti.get("command") or ""
    if not cmd.strip() or ti.get("run_in_background"):
        return H.ignore(d, "background command" if cmd.strip() else "empty command")

    held = S.hand_peek(log, "fg-live")
    if held:
        # Normally consumed by claude-cmd-fmt.py's PostToolUse handler — but a
        # MANUALLY CANCELLED command fires no hook at all (same gap noted throughout
        # this file), so that never runs and the record would otherwise wedge every
        # later command out of live-streaming forever. Tell a genuinely-in-flight
        # record (its tailer pid still alive) apart from an abandoned one the same way
        # claude_slots does for slots: a dead pid means it's stale, so clear it and
        # proceed as if there were none. (The record lives in the per-session state
        # DB — claude_state handoffs, key "fg-live" — was a .fg-live JSON file.)
        pid = held.get("pid") if isinstance(held, dict) else None
        stale = not (pid and S.pid_alive(pid))      # no pid recorded -> can't confirm, assume stale
        if not stale:
            A.hook_event(d, decision="ignored: a live fg block is already in flight")
            return                                  # a live fg block is genuinely still in flight
        S.hand_del(log, "fg-live")
        A.state_file(log, "state:fg-live", "remove-stale",
                     "dead tailer pid — record abandoned")

    # If the command already sends its own stdout to a file, tail THAT instead of
    # tee-ing into a second file (shared tokenizer — see claude_ops.parse_redirect).
    redirect = O.parse_redirect(cmd, d.get("cwd"))
    wrapped_cmd, own, append = None, False, False
    # The ".done" sentinel gets its own session-keyed /tmp path, NEVER derived from
    # the command's redirect target — deriving it from `src` used to drop stray
    # `<target>.done` files (even literal `$VAR.done`) into the project directory
    # whenever the command redirected to a relative path.
    stem = f"{log}.fg.{os.getpid()}.{int(time.time() * 1000)}"
    done = stem + ".done"

    if redirect:
        src, append = redirect
    else:
        src = stem + ".out"
        try:
            open(src, "a").close()
        except Exception:
            return
        own = True
        q = shlex.quote(src)
        wrapped_cmd = "{ " + cmd + "\n} > >(tee -a " + q + ") 2> >(tee -a " + q + " >&2)"

    if not os.path.exists(H.script("claude-stream.py")):
        if own:
            try: os.remove(src)
            except Exception: pass
        return

    # Claim a "fg" slot so claude-tab-status.py's bg_command_running() can see this
    # command is still running (via a live fg.<n> marker) — without this, a foreground
    # command running past the idle-watch threshold gets its tab wrongly flipped green
    # (and there'd be no way to notice a Ctrl+B-backgrounded command at all).
    slot, slot_marker = claude_slots.claim("fg", log)

    env = dict(os.environ)
    env["CLAUDE_STREAM_SRC"] = src
    env["CLAUDE_STREAM_DONE"] = done
    if own:
        env["CLAUDE_STREAM_OWN"] = "1"
    if append:
        # A `>>` redirect appends to an EXISTING file — tail only what this command
        # adds, or the whole prior file contents would be replayed into the mirror.
        env["CLAUDE_STREAM_SKIP_EXISTING"] = "1"
    proc = H.spawn_streamer("claude-stream.py",
                            ["fg", f"fg-{os.getpid()}-{int(time.time())}", log, slot],
                            log, env=env, purpose="stream:fg live tail",
                            audit_argv=["fg", str(slot)])
    if proc is None:
        claude_slots.release("fg", log, slot, os.getpid())
        if own:
            try: os.remove(src)
            except Exception: pass
        return
    claude_slots.set_owner(slot_marker, proc.pid)

    rec = {"src": src, "own": own, "pid": proc.pid, "done": done}
    if S.hand_put(log, "fg-live", rec):
        A.state_file(log, "state:fg-live", "write", rec)
    else:
        A.error(log, "write fg-live record", {"src": src})
        return                                     # tailer will notice via its own backstop eventually

    O.emit(log, O.blank(), O.rule(), O.label("▶ foreground", LBL_FG), O.code(cmd), O.rule())
    A.hook_event(d, decision="live fg stream: slot=%s tailer=%s %s"
                 % (slot, proc.pid, "rewrote command (tee)" if wrapped_cmd
                    else "tailing command's own redirect"))

    if wrapped_cmd:
        new_ti = dict(ti); new_ti["command"] = wrapped_cmd
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "updatedInput": new_ti,
        }}))


if __name__ == "__main__":
    H.run(main)
