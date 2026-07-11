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

    redirect = CT.parse_redirect(cmd, d.get("cwd"))
    stem = f"{log}.subfg.{tid}"
    done = stem + ".done"
    wrapped_cmd, own, append = None, False, False
    if redirect:
        src, append = redirect                       # tail the command's own redirect target
    else:
        src = stem + ".out"
        try:
            open(src, "a").close()
        except Exception:
            return H.ignore(d, "agent_id (could not create tee file)")
        own = True
        q = shlex.quote(src)
        # The blank line before "}" is load-bearing — see the identical wrap below.
        wrapped_cmd = "{ " + cmd + "\n\n} > >(tee -a " + q + ") 2> >(tee -a " + q + " >&2)"

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
        # permissionDecision "allow" is required for updatedInput to take effect (see
        # the main path below) — so a subagent's rewritten fg command is auto-approved
        # (deny rules still apply). Gated by CLAUDE_MIRROR_LIVE_FG_SUB above.
        new_ti = dict(ti); new_ti["command"] = wrapped_cmd
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "updatedInput": new_ti,
        }}))


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
    redirect = CT.parse_redirect(cmd, d.get("cwd"))
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
        # The blank line before "}" is load-bearing: a command ENDING in a
        # line-continuation backslash consumes the first newline, which used to
        # weld the closing "}" onto the last line — a syntax error for a command
        # that ran fine unwrapped. The extra newline gives it one to eat.
        wrapped_cmd = "{ " + cmd + "\n\n} > >(tee -a " + q + ") 2> >(tee -a " + q + " >&2)"

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

    # Content pretty-rendering: when this command streams a markdown or JSON file's
    # raw contents (cat/head/tail of a .md, cat of a .json, or `< file`), tell the
    # tailer to render the body instead of showing it verbatim. Gated (default-on)
    # by CLAUDE_MIRROR_MD / CLAUDE_MIRROR_JSON, mirroring CLAUDE_MIRROR_LIVE_FG.
    md = (os.environ.get("CLAUDE_MIRROR_MD", "1") != "0" and CT.md_source(cmd))
    js = (not md and os.environ.get("CLAUDE_MIRROR_JSON", "1") != "0"
          and CT.json_source(cmd))
    yaml = (not md and not js and os.environ.get("CLAUDE_MIRROR_YAML", "1") != "0"
            and CT.yaml_source(cmd))
    # code_source returns a pygments lexer name (e.g. "python") or None.
    code = (None if (md or js or yaml) or os.environ.get("CLAUDE_MIRROR_CODE", "1") == "0"
            else CT.code_source(cmd))

    env = dict(os.environ)
    env["CLAUDE_STREAM_SRC"] = src
    env["CLAUDE_STREAM_DONE"] = done
    if md:
        env["CLAUDE_STREAM_MD"] = "1"
    if js:
        env["CLAUDE_STREAM_JSON"] = "1"
    if yaml:
        env["CLAUDE_STREAM_YAML"] = "1"
    if code:
        env["CLAUDE_STREAM_CODE"] = code
    if gid:
        env["CLAUDE_STREAM_GROUP"] = gid
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
    A.hook_event(d, decision="live fg stream: slot=%s tailer=%s %s%s"
                 % (slot, proc.pid, "rewrote command (tee)" if wrapped_cmd
                    else "tailing command's own redirect",
                    " [md-render]" if md else " [json-render]" if js
                    else " [yaml-render]" if yaml
                    else (" [code-render:%s]" % code) if code else ""))

    if wrapped_cmd:
        # permissionDecision "allow" is DELIBERATE (owner's call, do not "fix"):
        # updatedInput only takes effect with "allow" (auto-approve) or "ask"
        # (prompt on EVERY rewritten command, even allowlisted ones — there is no
        # "rewrite, then fall through to normal permission rules" option). "ask"
        # is unusably noisy, so rewritten foreground commands never
        # permission-prompt; deny rules still apply. See README § Live foreground
        # streaming.
        new_ti = dict(ti); new_ti["command"] = wrapped_cmd
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "updatedInput": new_ti,
        }}))


def entry():
    H.run(main)
