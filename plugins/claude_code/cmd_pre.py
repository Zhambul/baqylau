# plugins/claude_code/cmd_pre.py — PreToolUse(Bash) live-fg rewrite
# Entry point: claude-cmd-pre.py (a thin shim — the entry FILENAME is the audit vocabulary).
# PreToolUse(Bash) hook: makes long foreground commands stream
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

from core import ops as O
from core import slots as claude_slots
from core import state as S
from plugins.claude_code import hookkit as H
from plugins.claude_code import tools as CT

A = O.A    # audit trail (real module, or a no-op stub if it failed to import)

LBL_FG = O.SLATE   # same colour claude-cmd-fmt.py uses for an OK foreground block


def _tee_wrap(cmd, src):
    # Wrap `cmd` so its stdout/stderr ALSO tee into `src` (the live-tail file).
    # The blank line before "}" is load-bearing: a command ENDING in a
    # line-continuation backslash consumes the first newline, which used to
    # weld the closing "}" onto the last line — a syntax error for a command
    # that ran fine unwrapped. The extra newline gives it one to eat.
    q = shlex.quote(src)
    return "{ " + cmd + "\n\n} > >(tee -a " + q + ") 2> >(tee -a " + q + " >&2)"


def _prepare_tee(cmd, stem, cwd):
    # Shared orchestration for both live-fg paths (main session + subagent):
    # if the command already redirects its own stdout to a file, tail THAT
    # (no rewrite); otherwise create `stem + ".out"` and tee into it.
    # Returns (src, own, append, wrapped_cmd) — wrapped_cmd is None on the
    # redirect path — or None if the tee file could not be created (the
    # caller picks its own failure response).
    redirect = CT.parse_redirect(cmd, cwd)
    if redirect:
        src, append = redirect                       # tail the command's own redirect target
        return src, False, append, None
    src = stem + ".out"
    try:
        open(src, "a").close()
    except Exception:
        return None
    return src, True, False, _tee_wrap(cmd, src)


def _emit_updated_input(ti, wrapped_cmd):
    # permissionDecision "allow" is DELIBERATE (owner's call, do not "fix"):
    # updatedInput only takes effect with "allow" (auto-approve) or "ask"
    # (prompt on EVERY rewritten command, even allowlisted ones — there is no
    # "rewrite, then fall through to normal permission rules" option). "ask"
    # is unusably noisy, so rewritten foreground commands never
    # permission-prompt; deny rules still apply. See docs/streaming.md, Live foreground
    # streaming. (Applies to both the main path and a subagent's fg command,
    # the latter gated by CLAUDE_MIRROR_LIVE_FG_SUB.)
    new_ti = dict(ti); new_ti["command"] = wrapped_cmd
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "allow",
        "updatedInput": new_ti,
    }}))


def sub_fg(d, log):
    # A SUBAGENT's foreground Bash command. claude-cmd-fmt.py still SKIPS agent_id
    # events (the substream owns subagent RENDERING), but the substream can only
    # show a subagent command's output once its tool_result lands in the transcript —
    # i.e. AFTER it finishes. To stream it live too, apply the same tee-rewrite trick
    # the main session uses, keyed by tool_use_id: leave a "subfg:<tid>" hand-off with
    # the tee paths, and claude-substream.py (which HAS the subagent's colour) spawns
    # the tailer when it reaches this tool_use and suppresses its own output render.
    # No fg slot is claimed (the tab is already blue via this agent's sub.pid row) and
    # no header is emitted (the substream emits its own, in the subagent's colour).
    if os.environ.get("CLAUDE_MIRROR_LIVE_FG_SUB", "1") == "0":
        return H.ignore(d, "agent_id (CLAUDE_MIRROR_LIVE_FG_SUB=0)")
    ti = d.get("tool_input") or {}
    cmd = ti.get("command") or ""
    tid = d.get("tool_use_id") or ""
    if not cmd.strip() or ti.get("run_in_background") or not tid:
        return H.ignore(d, "agent_id (not a live-fg subagent command)")

    stem = f"{log}.subfg.{tid}"
    done = stem + ".done"
    prep = _prepare_tee(cmd, stem, d.get("cwd"))
    if prep is None:
        return H.ignore(d, "agent_id (could not create tee file)")
    src, own, append, wrapped_cmd = prep

    rec = {"src": src, "done": done, "own": own, "append": append, "tid": tid}
    if not S.hand_put(log, "subfg:" + tid, rec):
        if own:
            try: os.remove(src)
            except Exception: pass
        A.error(log, "write subfg record", {"src": src, "tid": tid})
        return
    A.state_file(log, "state:subfg:" + tid, "write", rec)
    A.hook_event(d, decision="subagent live fg: marker written tid=%s (%s)"
                 % (tid[:8], "rewrote command (tee)" if wrapped_cmd else "own redirect"))

    if wrapped_cmd:
        _emit_updated_input(ti, wrapped_cmd)


def main():
    d, log = H.read_payload()
    if d is None:
        return
    if os.environ.get("CLAUDE_MIRROR_LIVE_FG", "1") == "0":
        return H.ignore(d, "CLAUDE_MIRROR_LIVE_FG=0")   # escape hatch if the rewrite ever misbehaves
    if d.get("agent_id"):
        return sub_fg(d, log)                           # a subagent's fg command -> live tee (below)
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
        # DB — core.state handoffs, key "fg-live" — was a .fg-live JSON file.)
        pid = held.get("pid") if isinstance(held, dict) else None
        stale = not (pid and S.pid_alive(pid))      # no pid recorded -> can't confirm, assume stale
        if not stale:
            A.hook_event(d, decision="ignored: a live fg block is already in flight")
            return                                  # a live fg block is genuinely still in flight
        S.hand_del(log, "fg-live")
        A.state_file(log, "state:fg-live", "remove-stale",
                     "dead tailer pid — record abandoned")

    # If the command already sends its own stdout to a file, tail THAT instead of
    # tee-ing into a second file (shared tokenizer — see plugins/claude_code/tools.parse_redirect).
    # The ".done" sentinel gets its own session-keyed /tmp path, NEVER derived from
    # the command's redirect target — deriving it from `src` used to drop stray
    # `<target>.done` files (even literal `$VAR.done`) into the project directory
    # whenever the command redirected to a relative path.
    stem = f"{log}.fg.{os.getpid()}.{int(time.time() * 1000)}"
    done = stem + ".done"
    prep = _prepare_tee(cmd, stem, d.get("cwd"))
    if prep is None:
        return
    src, own, append, wrapped_cmd = prep

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

    # The block's copy-group id (⧉ copy links): this tool call's id, stamped on
    # the header/code ops below and handed to the tailer so its gut/finish ops
    # join the same group — claude-copy.py collects a block by this id.
    gid = d.get("tool_use_id") or None

    # Content pretty-rendering (markdown/JSON/YAML/source colouring) is the
    # TAILER's decision, derived from the raw command passed here — the launch
    # site never computes it (see stream.py's _detect_render / hookkit.stream_env).
    env = H.stream_env(src=src, done=done, cmd=cmd, group=gid, own=own,
                       skip_existing=append)
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

    # "tid" keys this record to THIS tool call: PostToolUse consumes it only on a
    # matching tool_use_id, so a cancelled command's surviving record can't be
    # eaten by the next command's Post (which cross-wired the two blocks).
    rec = {"src": src, "own": own, "pid": proc.pid, "done": done,
           "tid": d.get("tool_use_id") or ""}
    if S.hand_put(log, "fg-live", rec):
        A.state_file(log, "state:fg-live", "write", rec)
    else:
        A.error(log, "write fg-live record", {"src": src})
        return                                     # tailer will notice via its own backstop eventually

    O.emit(log, O.blank(), O.rule(), O.label("▶ foreground", LBL_FG, g=gid),
           O.code(cmd, g=gid), O.rule())
    # (Any content-render mode the tailer picks is audited by the tailer itself —
    # the state_files "render:<taskid> start" row.)
    A.hook_event(d, decision="live fg stream: slot=%s tailer=%s %s"
                 % (slot, proc.pid, "rewrote command (tee)" if wrapped_cmd
                    else "tailing command's own redirect"))

    if wrapped_cmd:
        _emit_updated_input(ti, wrapped_cmd)   # "allow" is deliberate — see the helper


def entry():
    H.run(main)
