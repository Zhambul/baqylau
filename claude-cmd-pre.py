#!/usr/bin/env python3
# claude-cmd-pre.py — PreToolUse(Bash) hook: makes long foreground commands stream
# live in the mirror, the same way background commands already do.
#
# Claude Code only hands a foreground command's output to a hook at PostToolUse,
# once it has already finished (see claude-cmd-log.sh) — there's no other way to
# see it while it's still running. To fix that we rewrite the command here (via
# PreToolUse's `updatedInput`) so it ALSO tees its stdout/stderr into a side file,
# emit the "▶ foreground" header immediately, and spawn a detached claude-stream.py
# to tail that file — the exact mechanism background commands already use, just
# triggered a hook earlier. claude-cmd-fmt.py's PostToolUse handler checks the
# ".fg-live" marker this leaves behind and, if present, hands the finish chip off
# to the tailer (via a ".done" sentinel next to the tee file) instead of rendering
# the whole block itself.
#
# Only top-level (non-subagent, non-background) commands are wrapped: a subagent
# can run several foreground Bash calls in parallel, and this session's single
# ".fg-live" marker only tracks ONE in-flight command at a time. That's safe for
# the main loop, which always awaits one Bash call's Pre->exec->Post cycle before
# starting the next, but not for concurrent subagents — those keep the old bundled
# rendering (already excluded below via the agent_id check, same as claude-cmd-fmt.py).
import json, os, re, shlex, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import claude_ops as O
import claude_slots

A = O.A    # audit trail (real module, or a no-op stub if it failed to import)

LBL_FG = (170, 185, 210)  # slate — same colour claude-cmd-fmt.py uses for an OK foreground block


def parse_redirect(cmd, cwd):
    # Mirrors claude-cmd-fmt.py's parse_redirect: if the command already sends its
    # own stdout to a file, tail THAT instead of tee-ing into a second file.
    # Returns (absolute_target, append) or None.
    try:
        toks = shlex.split(cmd, posix=True)
    except ValueError:
        return None
    target, append, i = None, False, 0
    while i < len(toks):
        t = toks[i]
        if ">" in t and not t.startswith("2"):
            m = re.match(r"^(?:&|1)?(>>?)(.*)$", t)
            if m:
                rest = m.group(2)
                if rest:
                    target, append = rest, m.group(1) == ">>"
                elif i + 1 < len(toks):
                    target, append = toks[i + 1], m.group(1) == ">>"
                    i += 1
        i += 1
    if not target or target.startswith("&") or target.startswith("/dev/"):
        return None
    # shlex does NO shell expansion: a target holding $vars, backticks, globs, or a
    # leading ~ is not the path the shell will actually write to (`> "$OUT"` would
    # have us tail a literal file named $OUT). Fall back to the tee side file.
    if any(c in target for c in "$`*?[") or target.startswith("~"):
        return None
    if not os.path.isabs(target):
        target = os.path.join(cwd or os.getcwd(), target)
    return target, append


def main():
    try:
        d = json.load(sys.stdin)
    except Exception:
        return
    if os.environ.get("CLAUDE_MIRROR_LIVE_FG", "1") == "0":
        A.hook_event(d, decision="ignored: CLAUDE_MIRROR_LIVE_FG=0")
        return                                     # escape hatch if the rewrite ever misbehaves
    if d.get("agent_id"):
        A.hook_event(d, decision="ignored: agent_id")
        return
    ti = d.get("tool_input") or {}
    cmd = ti.get("command") or ""
    if not cmd.strip() or ti.get("run_in_background"):
        A.hook_event(d, decision="ignored: " + ("background command" if cmd.strip()
                                                else "empty command"))
        return

    log = O.log_path(d)
    marker = log + ".fg-live"
    if os.path.exists(marker):
        # Normally cleaned up by claude-cmd-fmt.py's PostToolUse handler — but a
        # MANUALLY CANCELLED command fires no hook at all (same gap noted throughout
        # this file), so that never runs and the marker would otherwise wedge every
        # later command out of live-streaming forever. Tell a genuinely-in-flight
        # marker (its tailer pid still alive) apart from an abandoned one the same way
        # claude_slots does for slots: a dead pid means it's stale, so clear it and
        # proceed as if there were no marker.
        stale = True
        try:
            with open(marker) as f:
                held = json.load(f)
            pid = held.get("pid")
            if pid:
                os.kill(pid, 0)
            stale = False
        except ProcessLookupError:
            stale = True
        except PermissionError:
            stale = False                           # exists, owned by someone else -> still alive
        except Exception:
            stale = True                            # unreadable / no pid recorded -> can't confirm, assume stale
        if not stale:
            A.hook_event(d, decision="ignored: a live fg block is already in flight")
            return                                  # a live fg block is genuinely still in flight
        try:
            os.remove(marker)
            A.state_file(log, marker, "remove-stale", "dead tailer pid — marker abandoned")
        except Exception:
            pass

    redirect = parse_redirect(cmd, d.get("cwd"))
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

    here = os.path.dirname(os.path.abspath(__file__))
    streamer = os.path.join(here, "claude-stream.py")
    if not os.path.exists(streamer):
        if own:
            try: os.remove(src)
            except Exception: pass
        return

    # Claim a "fg" slot so claude-tab-status.sh's bg_command_running() can see this
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
    try:
        proc = subprocess.Popen(
            [sys.executable, streamer, "fg", f"fg-{os.getpid()}-{int(time.time())}", log, str(slot)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True, env=env)
        A.spawn(log, proc.pid, [streamer, "fg", str(slot)], purpose="stream:fg live tail")
    except Exception:
        A.error(log, "spawn fg tailer", {"src": src})
        claude_slots.release("fg", log, slot, os.getpid())
        if own:
            try: os.remove(src)
            except Exception: pass
        return
    claude_slots.set_owner(slot_marker, proc.pid)

    try:
        with open(marker, "w") as f:
            json.dump({"src": src, "own": own, "pid": proc.pid, "done": done}, f)
        A.state_file(log, marker, "write", {"src": src, "own": own, "pid": proc.pid, "done": done})
    except Exception:
        A.error(log, "write .fg-live marker", {"src": src})
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
    try:
        main()
    except Exception:
        A.error("", "main")
