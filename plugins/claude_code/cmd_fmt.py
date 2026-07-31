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

from core import copy as C
from core import ops as O
from core import render as R
from core import slots as claude_slots
from core import state as S
from core import streamfmt as SF
from plugins.claude_code import fileobs as FOBS
from plugins.claude_code import hookkit as H
from plugins.claude_code import tools as CT

A = O.A    # audit trail (real module, or a no-op stub if it failed to import)

LOG = ""   # set in main() from the payload's session_id (per-session log)

# The finished-command chip colours now live in core/streamfmt (CMD_OK/BG/FAIL —
# shared with the codex exec block, docs/codex.md); the one still named locally is
# the slot-less background HEADER hue (not a finish outcome).
LBL_BG   = O.ORANGE   # background header chip / foreground "interrupted"


def _combined_output(tr):
    """A Bash tool_response's stdout+stderr as one rstripped block (stderr on its
    own line after stdout). `tr` is the tool_response: a dict on success, or a raw
    value we stringify."""
    out = tr.get("stdout", "") if isinstance(tr, dict) else str(tr)
    err = tr.get("stderr", "") if isinstance(tr, dict) else ""
    return (out + (("\n" + err) if err else "")).rstrip("\n")


def _observe(d, cmd, output, obs=None):
    """Run the OBSERVER command plane over a finished Bash command and return its
    audit-decision fragments (fileobs.cmd_matches — the memory wiki is the one row;
    docs/dashboard.md *Memory searches*). PostToolUse is where this belongs because
    the OUTPUT is half the record: a `qmd query` reads no file, and its answer —
    the ranked notes it came back with — exists nowhere but here. The mirror-side
    marker is cmd_pre's half (it owns the header op).

    `obs` is passed by a caller that already matched (it needed the marks); the
    default matches here. Runs after the block's own emit, like the file plane —
    the record functions are parked-guarded and never create the state DB."""
    obs = FOBS.cmd_matches(cmd, d.get("cwd")) if obs is None else obs
    frags = []
    for o in obs:
        frags += list(o.cmd_record(LOG, cmd, d.get("cwd"), output, None) or ())
    return frags


def _obs_note(frags):
    """Observer fragments as an audit-decision suffix ('' when nothing recorded)."""
    return "".join(" +" + f for f in frags)


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
    live = S.hand_take(LOG, S.FG_LIVE, match={"tid": d.get("tool_use_id") or ""})
    if live:
        A.state_file(LOG, "state:fg-live", "remove", live)
    # Hand-off key for giving the outcome to the fg tailer: the session-keyed token
    # claude-cmd-pre.py agreed on ("done"), never a path derived from the command's
    # own redirect target. The tailer polls the same key (CLAUDE_STREAM_DONE).
    done = (live.get("done") or (live["src"] + ".done")) if live and live.get("src") else None

    if bg or converted:
        return _render_background(d, cmd, taskid, converted, done)
    # A foreground file-reading command (sed/grep/cat of a source file, sed/grep of
    # a markdown one — CT.read_command) renders as a COLLAPSED Read one-liner, not a
    # streamed block. claude-cmd-pre.py skipped its live streaming (same gate), so
    # there is never a live tailer here (`live` is None); a FAILED command falls
    # through to the normal block so its error is shown, and an EMPTY-output run does
    # too (nothing to "read" — a normal "(no output)" block reads better than an
    # empty Read). The command + its output, rendered per kind, expand from a view
    # stash.
    if not live and not H.is_failure(d):
        spec, files, reader = CT.read_command(cmd)
        if files:
            output = _combined_output(tr)
            if output.strip():
                return _render_read(d, cmd, output, spec, files, reader)
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
                            g=taskid, act=O.ACT_BG))
    else:
        # OBSERVER command plane, same as the foreground paths — but with NO output:
        # a background command's bytes go to the tailer, not to this hook, so a
        # backgrounded `qmd query` records its QUESTION with no hits (the notes it
        # reads are named in the command and are recorded normally). Deliberately
        # not chased into the tailer: a bg vault search is a shape nobody runs.
        obs = FOBS.cmd_matches(cmd, d.get("cwd"))
        head = "▷ background" + "".join("  " + R.DIM + o.mark + R.RST for o in obs)
        O.emit(LOG, O.blank(), O.rule(),
               O.label(head, head_rgb, g=taskid, act=O.ACT_BG,
                       mem=FOBS.cmd_mem_flag(cmd, d.get("cwd"), obs)),
               O.code(cmd, g=taskid), O.rule())
        _observe(d, cmd, "", obs)

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
    else:
        body = _combined_output(tr)
        code = None
    # The block-closing chip + its colour — the shared shape (core/streamfmt) a
    # codex exec block paints identically: slate ok / red failed / orange
    # interrupted. One colour for the whole block, so the finish line matches the
    # gutter and you can tell which stream finished.
    chip_txt, col = SF.finish_chip(dur, failed=failed, interrupted=interrupted,
                                   exit_code=code)

    gut_body = R.emphasize(R.unescape(body)) if body else SF.no_output_body()

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

    # OBSERVER command plane (fileobs — the memory wiki is the one row). On the LIVE
    # path cmd_pre already marked the header and this call only adds the kv record;
    # when we render the whole block here (cmd_pre skipped it, or the command failed)
    # this is also where the ❖ marker and the header's `mem` flag come from.
    obs = FOBS.cmd_matches(cmd, d.get("cwd"))
    if not live:
        gid = d.get("tool_use_id") or None      # ⧉ copy links: this block's group
        head = "▶ foreground" + "".join("  " + R.DIM + o.mark + R.RST for o in obs)
        O.emit(LOG, *SF.command_block(cmd, gut_body, chip_txt, col, gid, head=head,
                                      mem=FOBS.cmd_mem_flag(cmd, d.get("cwd"), obs)))
    frags = _observe(d, cmd, body, obs)
    A.hook_event(d, decision=("handed off to fg tailer: " if live else "rendered: ")
                 + chip_txt + _obs_note(frags))

    # Update the session scoreboard. claude-scorebar.py (its own small window under
    # the mirror) refreshes off this sidecar bump — nothing is emitted into the log.
    # Token/cost spend is no longer folded here: the OTLP receiver (plugins/otel/)
    # is the authoritative cost source and updates the scoreboard live. Best-effort —
    # a failed bump must never break the command block above.
    O.bump(LOG, tool="Bash", commands=1, **({"failed": 1} if failed else {}))


def _read_body_code(output, spec, gid):
    """The `code` kind's stash body: ONE gut op carrying the RAW output plus the
    paint-time `lex` spec (the lexer detection already picked), syntax-highlighted
    in the RENDERER like any Read body — the same deferral, since this hook's
    python may lack pygments. Deliberately NO `num`: a sed/grep slice's true line
    numbers aren't recoverable from its output (`sed -n 120,400p` prints no
    numbers), and numbering it from 1 would assert a falsehood."""
    return [O.gut(output, O.BLUE, lex=spec.value, g=gid)]


def _read_body_md(output, _spec, gid):
    """The `md` kind's stash body: the markdown AST render (streamfmt.file_md_ops →
    core.mdrender — headings to amber banners, lists, tables, fenced code), so a
    `sed -n 120,400p CLAUDE.md` expands EXACTLY like a native Read of that extent
    — one shared builder, not a second markdown rendering. Prose is styled, not
    lexed, so these ops carry no `lex`; the trade is that ⧉out copies the RENDERED
    text rather than the raw bytes (identical to a native .md Read's expansion)."""
    return [dict(op, g=str(gid)) for op in SF.file_md_ops(output, O.BLUE)]


def _read_body_plain(output, _spec, gid):
    """Fallback body for a read-eligible kind with no builder of its own: the
    output verbatim. Never a stranded block — claude-cmd-pre.py already skipped
    streaming on the same read_command verdict, so this side MUST render
    something. (_READ_BODY covering every read-eligible kind is contract-tested.)"""
    return [O.gut(output, O.BLUE, g=gid)]


# The "pick a renderer per kind" table behind the Read one-liner's expansion,
# keyed by CT.ReadSpec.kind — the seam that replaced assuming a pygments lexer
# (a markdown read has none; it has an AST renderer).
_READ_BODY = {"code": _read_body_code, "md": _read_body_md}


def _stash_read_view(log, gid, names, cmd, output, spec, line):
    """Stash a file-reading command's block under kv `view:<gid>` and wrap `line`
    in the claude-copy:///…/view hyperlink — the same click-to-view protocol
    file_fmt.stash_view pins for file ops, but the block is a COMMAND (a `code`
    op, pretty-printed) + its rendered OUTPUT (the per-kind body from _READ_BODY:
    a lex `gut` op for source, markdown gut ops for a .md). The header label
    carries the group id + ⧉cmd/⧉out link specs so the expansion is copiable:
    core.copy.collect falls back to this stash (the block streams nothing to the
    ops table). Returns (hyperlinked line, gid), or (line, None) when the stash
    write failed (the caller keeps the plain line).

    `names` is EVERY file the command read, and the header names them ALL — which is
    the honest shape for a multi-file read, because the body below is ONE undivided
    output stream (`cat a.py b.py` emits no delimiter between them). Naming both
    over one blob says "these two files, this output"; naming one would claim the
    blob was that file's."""
    body = _READ_BODY.get(spec.kind, _read_body_plain)(output, spec, gid)
    vops = [O.rule(),
            O.label("Read " + " ".join(names), O.BLUE, g=gid,
                    lk=[["cmd", "⧉cmd"], ["out", "⧉out"]]),
            O.code(cmd, g=gid),
            *body,
            O.blank()]
    # Parking the block, linking the line and auditing the stash is the
    # click-to-view protocol itself, owned by core.copy (the toggle's other
    # half); what is this producer's own is the BLOCK above.
    return C.stash(log, gid, vops, line,
                   {"tool": "Bash", "kind": "read", "render": spec.kind})


def _render_read(d, cmd, output, spec, files, reader):
    """Render a file-reading foreground command as a collapsed Read one-liner: a
    blue Read(name) line (the shared streamfmt.file_line shape, so it reads like a
    real Read) with a dim reader tag (`sed`/`grep`/…), clickable to expand the
    command + its rendered output (_stash_read_view — highlighted source for the
    `code` kind, markdown for `md`). The reader tag keeps it honest — it is a
    command, not a native Read — while the noise (the streamed file dump) collapses
    behind the click, ⧉cmd/⧉out copiable.

    SEVERAL files (`cat app.py utils.py`, `sed a.md; sed b.md`) stay ONE block, whose
    line LISTS them (up to streamfmt.FILE_LIST_MAX, then a dim `+N`). One block
    because there is one output: which bytes came from which file is not recoverable
    (docs/mirror-pane.md — the rejected splitting designs are recorded there), so N
    lines would each have to claim the whole blob. But every file is accounted for —
    listed on the line, all of them in the expansion's header, all of them in the
    scoreboard's file set, and the total on the op's `nf` field so the web's
    collapsed summary says "Read 2 files". What was actually lost before: `cat
    app.py utils.py` reported only app.py."""
    path = files[0]
    _disp, loc = SF.file_display(path, d.get("cwd"))
    names = [os.path.basename(f.rstrip("/")) or f for f in files]
    # The whole LIST goes to the shared builder, which owns the one-liner shape —
    # how many names it lists, the separator, and the `+N` for the remainder
    # (streamfmt.FILE_LIST_MAX). Each name is location-aware exactly as a single
    # file op's is (file_display: bare basename under the session cwd, ✎ for a
    # scratchpad, a dim abbreviated dir for anything else).
    disps = [SF.file_display(f, d.get("cwd"))[0] for f in files]
    line = SF.file_line("Read", disps, O.BLUE)
    if reader:
        line += "  " + R.DIM + reader + R.RST
    # OBSERVER command plane (fileobs — memory the one row): this one-liner IS the
    # whole block, so its marker rides the line like a file op's, and the `mem` flag
    # goes on the `line` op rather than a header (there is none).
    obs = FOBS.cmd_matches(cmd, d.get("cwd"))
    for o in obs:
        line += "  " + R.DIM + o.mark + R.RST
    gid = d.get("tool_use_id") or None
    vid = None
    if gid:
        line, vid = _stash_read_view(LOG, gid, names, cmd, output, spec, line)
    O.emit(LOG, O.line(line, view=vid, mem=bool(obs), nfiles=len(files),
                       act=O.ACT_READ))
    # It is still a Bash command — count it as one (not a file read), matching how
    # the normal foreground path bumps. The OTLP receiver owns token/cost. Each file
    # IS fed to the scoreboard's UNIQUE-path `files` set though: the command read
    # them, and that counter is about which files a session touched.
    O.bump(LOG, tool="Bash", commands=1)
    for f in files:
        O.bump(LOG, file=f)
    frags = _observe(d, cmd, output, obs)
    A.hook_event(d, decision="rendered as Read (%s): Read(%s) via %s"
                 % (spec.kind, " ".join(names), reader or "<stdin>")
                 + (f" [{loc}]" if loc else "")
                 + ("" if spec.value or spec.kind != "code"
                    else " [mixed lexers — plain body]")
                 + (" +view" if vid else "") + _obs_note(frags))


def entry():
    H.run(main)
