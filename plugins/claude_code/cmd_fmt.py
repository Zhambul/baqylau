# plugins/claude_code/cmd_fmt.py — PostToolUse(Bash) formatter
# Entry point: claude-cmd-fmt.py (a thin shim — the entry FILENAME is the audit vocabulary).
# claude-cmd-fmt.py — formatter for the kitty command-mirror pane.
#
# Reads a Claude Code PostToolUse(Bash) hook payload (JSON) on stdin and appends
# a formatted block (command | output | elapsed) to the mirror log given as
# argv[1], wrapped to the pane width given as argv[2]. Invoked directly as
# the PostToolUse(Bash) hook. The bash/python highlighting, gutter-wrapping, and escape
# handling live in core.render (shared with claude-substream.py).
#
# Subagent (Task/Agent) tool calls fire this same hook (with an agent_id), but the
# subagent's whole transcript is streamed in order by claude-substream.py instead,
# so we IGNORE agent_id events here to avoid double-rendering / mis-ordering.
import os, re
from urllib.parse import quote

from core import ops as O
from core import paths as PATHS
from core import render as R
from core import slots as claude_slots
from core import state as S
from core import streamfmt as SF
from plugins.claude_code import hookkit as H
from plugins.claude_code import tools as CT

A = O.A    # audit trail (real module, or a no-op stub if it failed to import)

LOG = ""   # set in main() from the payload's session_id (per-session log)

LBL_FG   = O.SLATE    # foreground OK (neutral, distinct from the vivid palettes)
LBL_BG   = O.ORANGE   # background header chip / foreground "interrupted"
LBL_FAIL = O.RED      # a failed tool (PostToolUseFailure)


def _spawn_stream(kind, taskid, slot, src=None, skip_existing=False, group=None,
                  cmd=None, pos0=None):
    # Launch claude-stream.py detached so it keeps tailing the job's output file
    # after this hook exits. Passes the claimed slot so its gutter + finish chip
    # match the header colour. If the command redirected stdout to a file (`src`),
    # hand it to the streamer via env so it tails that instead of the empty task
    # output file. `skip_existing` is for a Ctrl+B conversion handoff: the
    # departing fg tailer already showed whatever came through its own tee copy, so
    # the replacement bg tailer should skip whatever's already in the task's output
    # file rather than re-showing it from the start. Returns the Popen (or None).
    if not taskid:
        return None
    env = H.stream_env(src=src, cmd=cmd, group=group, skip_existing=skip_existing,
                       pos0=pos0)
    return H.spawn_streamer("claude-stream.py", [kind, taskid, LOG, slot], LOG,
                            env=env, purpose=f"stream:{kind} task={taskid}",
                            audit_argv=[kind, taskid, str(slot)])


def main():
    global LOG
    d, LOG = H.read_payload()
    if d is None:
        return
    # A subagent's tool calls are rendered (in transcript order, with messages) by
    # claude-substream.py — skip them here so they aren't rendered twice.
    if d.get("agent_id"):
        return H.ignore(d, "agent_id (substream owns rendering)")
    ti  = d.get("tool_input") or {}
    tr  = d.get("tool_response") or {}
    cmd = (ti.get("command") or "")
    if not cmd.strip():
        return H.ignore(d, "empty command")
    bg = bool(ti.get("run_in_background"))
    # Ctrl+B mid-command: the model asked for a plain foreground run, but the USER
    # backgrounded it before it finished. Claude Code reports this the same way as a
    # real completion — this Bash call's own PostToolUse fires right away, with a
    # `duration_ms` covering only the time UP TO the keypress — but tool_response
    # carries backgroundTaskId (+ backgroundedByUser) so it can be told apart from an
    # actually-finished command. Confirmed empirically: this is undocumented.
    taskid = tr.get("backgroundTaskId") if isinstance(tr, dict) else None
    converted = bool(taskid) and not bg

    # A foreground command's own live-stream record (claude-cmd-pre.py), if any —
    # consumed atomically up front (state-DB handoff, key "fg-live" — was a .fg-live
    # JSON file read+removed in two racy steps) since the genuine/converted-background
    # path below and the ordinary finish path further down both need to know about it.
    # match=tid: consume ONLY this tool call's record. A cancelled command fires no
    # hook, so its fg-live record survives (tailer still alive in its grace window) —
    # an unconditional take here let the NEXT Bash call's Post eat it and write its
    # own outcome into the cancelled command's block while itself never rendering.
    # A mismatched record is left alone: its tailer finishes via writer-liveness and
    # removes it itself, and this call just renders normally (live=None).
    live = S.hand_take(LOG, "fg-live", match={"tid": d.get("tool_use_id") or ""})
    if live:
        A.state_file(LOG, "state:fg-live", "remove", live)
    # Hand-off key for giving the outcome to the fg tailer: the session-keyed token
    # claude-cmd-pre.py agreed on ("done"), never a path derived from the command's
    # own redirect target. The tailer polls the same key (CLAUDE_STREAM_DONE).
    done = (live.get("done") or (live["src"] + ".done")) if live and live.get("src") else None

    if bg or converted:
        return _render_background(d, cmd, taskid, converted, done)
    # A foreground code-reading command (sed/grep/cat of a source file —
    # CT.read_command) renders as a COLLAPSED Read one-liner, not a streamed
    # block. claude-cmd-pre.py skipped its live streaming (same gate), so there is
    # never a live tailer here (`live` is None); a FAILED command falls through to
    # the normal block so its error is shown, and an EMPTY-output run does too
    # (nothing to "read" — a normal "(no output)" block reads better than an empty
    # Read). The command + its syntax-highlighted output expand from a view stash.
    if not live and not H.is_failure(d):
        lexer, path, reader = CT.read_command(cmd)
        if lexer:
            out = tr.get("stdout", "") if isinstance(tr, dict) else str(tr)
            err = tr.get("stderr", "") if isinstance(tr, dict) else ""
            output = (out + (("\n" + err) if err else "")).rstrip("\n")
            if output.strip():
                return _render_read(d, cmd, output, lexer, path, reader)
    _render_finished(d, tr, cmd, live, done)


def _render_background(d, cmd, taskid, converted, done):
    """A background launch (genuine run_in_background, or a Ctrl+B conversion):
    write the header, hand the rest of the block to a detached bg tailer."""
    # Claim a palette slot now and colour the "▷ background" header with it, so
    # this job's header, gutter, and finish chip all share one colour and the
    # parallel jobs differ. The streamer (passed the slot) does gutter + finish.
    if taskid:
        slot, slot_marker = claude_slots.claim("bg", LOG)
        head_rgb = claude_slots.color("bg", slot)
    else:
        slot, slot_marker, head_rgb = None, None, LBL_BG

    if converted and done:
        # ORDERING (deliberate — docs/streaming.md "Hand-off ordering"): the
        # converted sentinel goes out FIRST, pos0 is measured after it, below.
        # pos0-first would widen the theoretical dupe window (a smaller pos0
        # replays task-file content the departing tee copy already showed).
        # Our own fg tailer was tee-ing this command's own side file — but once
        # Ctrl+B hands it off, Claude Code captures further output into its OWN
        # backgroundTaskId file instead (empirically: our tee file gets nothing
        # more from this point on), so tell that tailer to bow out quietly (no
        # finish chip, no fallback body) instead of racing the bg tailer below,
        # which is about to own the rest of this block.
        if S.hand_put(LOG, "done:" + done, {"converted": True}):
            A.state_file(LOG, "state:done:" + done, "write", {"converted": True})
        else:
            A.error(LOG, "write converted handoff", {"done": done})
        O.emit(LOG, O.label("▷ backgrounded (ctrl+b) — continuing below", LBL_BG,
                            g=taskid))
    else:
        O.emit(LOG, O.blank(), O.rule(), O.label("▷ background", head_rgb, g=taskid),
               O.code(cmd, g=taskid), O.rule())

    O.bump(LOG, tool="Bash", commands=1)     # count it; the streamer owns its finish
    if taskid:
        # Converted: find_file() locates tasks/<taskid>.output itself, same as any
        # genuine background command — this cmd string's own redirect (if any) is
        # irrelevant to where Claude Code is now writing the real output.
        redirect = None if converted else CT.parse_redirect(cmd, d.get("cwd"))
        src, src_append = redirect if redirect else (None, False)
        # skip_existing for a `>>` redirect: tail only what this job appends, or
        # the target file's entire prior contents would replay into the mirror.
        # For a conversion the skip offset is measured NOW, against the task
        # output file located by the same glob the tailer uses (0 if it doesn't
        # exist yet): the departing fg tee showed everything up to THIS moment,
        # so everything after it belongs to the bg block — leaving the tailer to
        # measure at its own open time skipped output that landed during its
        # startup (hookkit.stream_env, CLAUDE_STREAM_POS0).
        pos0 = None
        if converted:
            from plugins.claude_code import stream as ST
            found = ST.glob_task_output(taskid)
            pos0 = 0
            if found:
                try:
                    pos0 = os.path.getsize(found)
                except OSError:
                    pos0 = 0
        proc = _spawn_stream("bg", taskid, slot, src,
                             skip_existing=converted or src_append, group=taskid,
                             cmd=cmd, pos0=pos0)
        if proc is not None:
            claude_slots.set_owner(slot_marker, proc.pid)
        else:
            claude_slots.release("bg", LOG, slot, os.getpid())
    A.hook_event(d, decision=("converted ctrl+b -> bg tailer" if converted
                              else "background: tailer spawned")
                 + f" task={taskid or '?'} slot={slot}")


def _render_finished(d, tr, cmd, live, done):
    """A foreground command's real outcome: hand it to the live fg tailer when one
    exists (it owns the block), else render the whole block here."""
    ms  = d.get("duration_ms")
    dur = "?" if ms is None else O.fmt_dur(ms / 1000)
    failed = H.is_failure(d)
    interrupted = bool(d.get("is_interrupt"))

    if failed:
        # A failed tool has no tool_response; its combined output (often prefixed
        # "Exit code N") is in the top-level `error` field. Pull the exit code
        # into the chip so it isn't duplicated in the body.
        body = (d.get("error") or "").rstrip("\n")
        m = re.match(r"Exit code (\d+)\n?", body)
        code = m.group(1) if m else None
        if m:
            body = body[m.end():]
        if interrupted:
            chip_txt, col = "■ interrupted · " + dur, LBL_BG
        elif code is not None:
            chip_txt, col = f"■ failed (exit {code}) · {dur}", LBL_FAIL
        else:
            chip_txt, col = "■ failed · " + dur, LBL_FAIL
    else:
        out = tr.get("stdout", "") if isinstance(tr, dict) else str(tr)
        err = tr.get("stderr", "") if isinstance(tr, dict) else ""
        body = (out + (("\n" + err) if err else "")).rstrip("\n")
        chip_txt, col = "■ finished · " + dur, LBL_FG

    # One colour for the whole block — header, gutter, and finish chip all use it
    # (slate ok / red failed / orange interrupted), so the finish line matches the
    # gutter and you can tell which stream finished.
    gut_body = R.emphasize(R.unescape(body)) if body else R.DIM + "(no output)" + R.RST

    # claude-cmd-pre.py (PreToolUse) may already have rendered the header and be
    # tailing this command's output live (see its module docstring; `live` was read
    # further up, before the bg/converted branch above). If so, this is the only
    # place the REAL outcome (duration/exit code/interrupted) is known, so hand it to
    # that tailer via a sentinel instead of re-rendering the header + body ourselves —
    # it also carries gut_body as a fallback in case the rewrite never took effect and
    # nothing was ever streamed.
    if done:
        if S.hand_put(LOG, "done:" + done,
                      {"chip": chip_txt, "color": list(col), "fallback_body": gut_body}):
            A.state_file(LOG, "state:done:" + done, "write", {"chip": chip_txt})
        else:
            A.error(LOG, "write done handoff", {"done": done})
            live = None    # couldn't hand off -> fall through to the normal render below

    if not live:
        gid = d.get("tool_use_id") or None      # ⧉ copy links: this block's group
        O.emit(LOG, O.blank(), O.rule(), O.label("▶ foreground", col, g=gid),
               O.code(cmd, g=gid), O.rule(), O.gut(gut_body, col, g=gid), O.rule(),
               O.label(chip_txt, col, g=gid), O.rule())
    A.hook_event(d, decision=("handed off to fg tailer: " if live else "rendered: ")
                 + chip_txt)

    # Update the session scoreboard. claude-scorebar.py (its own small window under
    # the mirror) refreshes off this sidecar bump — nothing is emitted into the log.
    # Token/cost spend is no longer folded here: the OTLP receiver (plugins/otel/)
    # is the authoritative cost source and updates the scoreboard live. Best-effort —
    # a failed bump must never break the command block above.
    O.bump(LOG, tool="Bash", commands=1, **({"failed": 1} if failed else {}))


def _stash_read_view(log, gid, name, cmd, output, lexer, line):
    """Stash a code-reading command's block under kv `view:<gid>` and wrap `line`
    in the claude-copy:///…/view hyperlink — the same click-to-view protocol
    file_fmt.stash_view pins for file ops, but the block is a COMMAND (a `code`
    op, pretty-printed) + its syntax-highlighted OUTPUT (a `gut` op carrying the
    raw text + a paint-time `lex` spec, highlighted in the renderer like a Read
    body). The header label carries the group id + ⧉cmd/⧉out link specs so the
    expansion is copiable: core.copy.collect falls back to this stash (the block
    streams nothing to the ops table). Returns (hyperlinked line, gid), or
    (line, None) when the stash write failed (the caller keeps the plain line)."""
    vops = [O.rule(),
            O.label("Read " + name, O.BLUE, g=gid,
                    lk=[["cmd", "⧉cmd"], ["out", "⧉out"]]),
            O.code(cmd, g=gid),
            O.gut(output, O.BLUE, lex=lexer, g=gid),
            O.blank()]
    if not S.kv_set(log, "view:" + str(gid), vops):
        return line, None
    url = "claude-copy:///%s/%s/view" % (
        quote(PATHS.sid_from_log(log), safe=""), quote(str(gid), safe=""))
    A.state_file(log, S.db_path(log), "view-stash",
                 {"gid": gid, "tool": "Bash", "kind": "read", "ops": len(vops)})
    return R.hyperlink(url, line), gid


def _render_read(d, cmd, output, lexer, path, reader):
    """Render a code-reading foreground command as a collapsed Read one-liner: a
    blue Read(name) line (the shared streamfmt.file_line shape, so it reads like a
    real Read) with a dim reader tag (`sed`/`grep`/…), clickable to expand the
    command + its highlighted output (_stash_read_view). The reader tag keeps it
    honest — it is a command, not a native Read — while the noise (the streamed
    file dump) collapses behind the click, ⧉cmd/⧉out copiable."""
    disp, loc = SF.file_display(path, d.get("cwd"))
    name = os.path.basename(path.rstrip("/")) or path
    line = SF.file_line("Read", disp, O.BLUE)
    if reader:
        line += "  " + R.DIM + reader + R.RST
    gid = d.get("tool_use_id") or None
    vid = None
    if gid:
        line, vid = _stash_read_view(LOG, gid, name, cmd, output, lexer, line)
    O.emit(LOG, O.line(line, view=vid))
    # It is still a Bash command — count it as one (not a file read), matching how
    # the normal foreground path bumps. The OTLP receiver owns token/cost.
    O.bump(LOG, tool="Bash", commands=1)
    A.hook_event(d, decision="rendered as Read: Read(%s) via %s"
                 % (name, reader or "<stdin>")
                 + (f" [{loc}]" if loc else "")
                 + (" +view" if vid else ""))


def entry():
    H.run(main)
