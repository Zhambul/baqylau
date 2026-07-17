# plugins/codex/stream.py — argv: MIRROR_LOG "r,g,b" SRCFILE JSONFILE LABEL
# Entry point: claude-codex-stream.py (a thin shim — the entry FILENAME is the
# audit vocabulary; spawned per discovered run by plugins/codex/watch.py).
#
# Detached tailer for ONE codex run, rendered into the kitty command-mirror pane.
# Spawned by claude-codex-watch.py (which discovers the run and picks the colour). It
# handles BOTH codex sources so EVERY codex call shows — the mode is auto-detected
# from SRCFILE's extension:
#
#   companion (.log)  — a codex-plugin companion job (`codex-companion.mjs`: review,
#                       adversarial-review, task, stop-gate; from the main agent, a
#                       subagent, a teammate, a slash command). Its human-readable
#                       activity log is `…/state/<slug>/jobs/<jobId>.log`; the sidecar
#                       `<jobId>.json` `status` (JSONFILE) is the completion signal.
#   rollout (.jsonl)  — codex's OWN native session log
#                       `~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl`,
#                       written for ANY codex run — incl. a raw `codex` / `codex exec`
#                       that never touched the companion. JSONFILE is "-"; completion
#                       is a `task_complete` event with no follow-up turn.
#                       Rollout-line PARSING lives in plugins/codex/rollout.py (the
#                       parse half of the parse/paint split, docs/sessionapi.md) —
#                       this renderer consumes its typed records and owns only the
#                       paint (chips, caps, colours, scoreboard folds).
#
# The colour is passed in as "r,g,b" (the watcher round-robins core.slots.CODEX_
# PALETTE) — this stream keeps no slot marker, so it never affects the tab colour.
# A codex run is attributed to the SESSION / cwd, not the launching agent_id, so it
# reads as its own top-level stream (rule-bracketed) in the codex palette.
import json, os, re, sys, time

from core import ops as O
from core import render as R
from core import state as S
from core import streamfmt as SF
from core import tail as T
from plugins.codex import rollout as RO

A = O.A    # audit trail (real module, or a no-op stub if it failed to import)

RST, FAIL = R.RST, R.fg(*O.RED)

# --- run identity (argv contract) ---------------------------------------------------
# All of this used to be parsed at module top level — importing the module read
# argv. It now lives in _init(), called from entry(), so IMPORTING this module
# (tests, tooling) reads no argv — only running it does. The placeholders below
# just name the module globals every function reads at call time.
LOG      = ""
SLOT_RGB = (0, 200, 150)
LOGFILE  = ""
JSONF    = "-"
LABEL    = "task"
ROLLOUT  = False                          # LOGFILE ends .jsonl; else companion .log


def _init(argv):
    """Bind this run's identity from the shim's argv:
      claude-codex-stream.py MIRROR_LOG "r,g,b" SRCFILE JSONFILE LABEL"""
    global LOG, SLOT_RGB, LOGFILE, JSONF, LABEL, ROLLOUT
    LOG      = argv[1] if len(argv) > 1 else ""
    SLOT_RGB = tuple(int(x) for x in argv[2].split(",")) if len(argv) > 2 else (0, 200, 150)
    LOGFILE  = argv[3] if len(argv) > 3 else ""
    JSONF    = argv[4] if len(argv) > 4 else "-"
    LABEL    = argv[5] if len(argv) > 5 else "task"
    ROLLOUT  = LOGFILE.endswith(".jsonl")

# Line caps per excerpt kind (how many lines of each block the mirror shows before
# "… (+N lines)"). These deliberately DIVERGE from plugins/claude_code/
# substream_render.py's caps — the two renderers weight their content differently;
# don't unify the values.
CAP_MSG       = 40  # an assistant message
CAP_OUTPUT    = 80  # review / final output
CAP_SUB       = 20  # a codex subagent line
CAP_REASONING = 16  # a companion "Reasoning summary" block
CAP_THINK     = 12  # a rollout agent_reasoning event
CAP_PROMPT    = 6   # the user prompt (rollout user_message)
CAP_HEAD      = 4   # a bare head line (review-started, search query, unknown)

# Approximate per-MTok (input, output) USD for codex models — the plugin's own
# price table (core deliberately has none; each tool plugin knows its vendor's
# rates). Cached input bills 0.1× input. Matching is by version-exact prefix —
# `key == model` or `model.startswith(key + "-")` — NOT substring, so an
# UNVERIFIED newer version (e.g. gpt-5.3-codex) falls through to "no cost
# shown" rather than being silently priced at an older rate; the bump-agent
# audit meta still records the token split, so spend is re-derivable once the
# rate is added here.
CODEX_PRICES = (
    ("gpt-5.1-codex-mini", 0.25, 2.00),
    ("gpt-5-mini",         0.25, 2.00),
    ("gpt-5-nano",         0.05, 0.40),
    ("gpt-5.1-codex",      1.25, 10.0),
    ("gpt-5-codex",        1.25, 10.0),
    ("gpt-5.1",            1.25, 10.0),
    ("gpt-5",              1.25, 10.0),
)


def codex_cost_usd(model, fresh_in, out, cached):
    m = (model or "").lower().strip()
    if not m:
        return None
    for key, pin, pout in CODEX_PRICES:
        if m == key or m.startswith(key + "-"):
            return (fresh_in * pin + cached * pin * 0.1 + out * pout) / 1_000_000
    return None


# File-op verbs + colours for a codex apply_patch, mirroring the Claude file-op
# look (claude-file-fmt / the substream) so an edit reads the same whoever made
# it. Scoreboard bumps use the matching Claude tool keys (Edit/Write) so the
# tools row tallies team-wide edits in one place, same as subagents do.
FILE_VERB = {"add": ("Write", O.GREEN, "Write"),
             "update": ("Update", O.YELLOW, "Edit"),
             "delete": ("Delete", O.RED, "Edit"),
             "move": ("Update", O.YELLOW, "Edit")}


def render_patch(rec):
    """A parsed rollout `patch` record (plugins/codex/rollout.py — built from
    patch_apply_end, the authoritative file-op record for a codex run: it
    carries the RESOLVED absolute paths + per-file diffs; the apply_patch
    response_item only has repo-relative patch text, so the parser ignores
    that one: rendering both would duplicate). One file-op line per changed
    file + the same scoreboard accounting the substream does for subagent
    file ops (unique-path files set, ± line sums, Edit/Write tool tallies) —
    plain bump() rows, no meta: these are file/line deltas, not the
    token/cost deltas the unattributed-bump anomaly guards."""
    if not rec["success"]:
        O.emit(LOG, O.gut(FAIL + "■ patch failed" + RST, SLOT_RGB))
        return
    for f in rec["files"]:
        verb, rgb, tool = FILE_VERB.get(f["change"], FILE_VERB["update"])
        name = os.path.basename((f["path"] or "").rstrip("/")) or f["path"] or "?"
        # The one-liner shape is the shared core builder (streamfmt.file_line —
        # the same anatomy the claude_code file formatters paint); a codex patch
        # has no extent/range/failure variants, just the ± counts.
        line = SF.file_line(verb, name, rgb, added=f["added"], removed=f["removed"])
        O.emit(LOG, O.gut(line, SLOT_RGB))
        O.bump(LOG, tool=tool, file=f["path"], added=f["added"], removed=f["removed"])

# A companion job-log line is prefixed with an ISO timestamp; the tail is the event
# head. Un-prefixed lines are continuation body of the preceding block event.
TS = re.compile(r"^\[\d{4}-\d\d-\d\dT[\d:.]+Z\]\s?(.*)$")


# Block shapes shared with the substream renderer (core/streamfmt.py), bound to
# this stream's identity. chip's g + lk tie a block's header to its code/gut body
# for the ⧉ copy handler — a fresh O.new_group() per block (codex records carry
# no tool_use_id). Same affordance the claude-session mirror paints (core/copy.py).
cap = SF.cap


def chip(glyph, kind, g=None, lk=None):
    return SF.chip("codex", glyph, kind, SLOT_RGB, g=g, lk=lk)


def gutter(text, g=None):
    return SF.gutter(text, SLOT_RGB, g=g)


def dim_gut(text, g=None):
    return SF.dim_gut(text, SLOT_RGB, g=g)


class Renderer:
    """Per-run mutable render state for BOTH sources (companion + rollout) —
    was ~10 module globals mutated via `global` in render_record/feed_rollout;
    gathering them here matches the substream_render.py house shape (a
    state-holding class the lifecycle instantiates per run)."""

    def __init__(self):
        self.last_msg = ""    # last assistant-message body, to de-dup a repeated "Final output"
        # companion: the `[ts]` block currently being accumulated (a head only
        # renders when the NEXT timestamped line flushes it)
        self.cur_head, self.cur_body = None, []
        # rollout lifecycle + accounting
        self.ro_started = self.ro_completed = self.ro_done_wall = None
        self.ro_active = False
        self.ro_aborted = False
        self.ro_model = ""    # bare model id from turn_context — prices the footer
        self.ro_tag = ""      # "model · effort" chip last shown (re-shown on change)
        self.ro_usage = None  # CUMULATIVE total_token_usage from the last token_count
        self.ro_malformed = 0  # complete-but-unparseable rollout lines this run

    def _emit_exit_chip(self, code):
        # The red failed-exit chip, shared by both sources (companion
        # "Command failed (exit N)" heads and rollout function_call_output
        # records — the extraction regexes legitimately differ per-site).
        O.emit(LOG, O.gut(FAIL + "■ exit " + code + RST, SLOT_RGB))

    # --- companion (.log) parse: the pre-digested `[ts] …` activity stream ----------
    # Kept as a prefix-match LADDER, deliberately not a dispatch table: the
    # branches match by startswith with overlapping prefixes ("Assistant message
    # captured:" must be tested before "Assistant message"), so ordering is
    # load-bearing — a name-keyed table would have to re-encode it.
    def render_record(self, head, body):
        head = (head or "").rstrip()
        if not head or head.startswith("Assistant message captured:"):
            return
        if head.startswith(("Thread ready", "Turn started", "Turn completed",
                            "Starting Codex", "Queued", "Reviewer finished")):
            return
        if head.startswith("Running command:"):
            g = O.new_group(LOG)
            # cmd-only link: codex's exit-code output lands in a separate record, not this
            # group, so there's no ⧉out body to offer.
            O.emit(LOG, chip("▶", "cmd", g=g, lk=[["cmd", "⧉cmd"]]),
                   O.code(head[len("Running command:"):].strip(), g=g))
            return
        if head.startswith(("Command completed:", "Command failed:")):
            m = re.search(r"\(exit (\d+)\)", head)
            if m and m.group(1) != "0":
                self._emit_exit_chip(m.group(1))
            return
        if head.startswith("Reviewer started"):
            what = head.split(":", 1)[-1].strip() if ":" in head else head
            g = O.new_group(LOG)
            O.emit(LOG, chip("◆", "review", g=g, lk=O.COPY_ALL), gutter(cap(what, CAP_HEAD), g=g))
            return
        body_text = "\n".join(body).strip()
        if head == "Assistant message":
            if body_text:
                self.last_msg = body_text
                g = O.new_group(LOG)
                O.emit(LOG, chip("✎", "message", g=g, lk=O.COPY_ALL),
                       gutter(cap(body_text, CAP_MSG), g=g))
            return
        if head == "Reasoning summary":
            if body_text:
                g = O.new_group(LOG)
                O.emit(LOG, chip("⋯", "reasoning", g=g, lk=O.COPY_ALL),
                       dim_gut(cap(body_text, CAP_REASONING), g=g))
            return
        if head == "Review output":
            if body_text:
                g = O.new_group(LOG)
                O.emit(LOG, chip("⇠", "review", g=g, lk=O.COPY_ALL),
                       gutter(cap(body_text, CAP_OUTPUT), g=g))
            return
        if head == "Final output":
            if body_text and body_text != self.last_msg:
                g = O.new_group(LOG)
                O.emit(LOG, chip("⇠", "result", g=g, lk=O.COPY_ALL),
                       gutter(cap(body_text, CAP_OUTPUT), g=g))
            return
        if head.startswith("Subagent "):
            g = O.new_group(LOG)
            O.emit(LOG, chip("✎", "sub", g=g, lk=O.COPY_ALL),
                   gutter(cap(body_text or head, CAP_SUB), g=g))
            return
        O.emit(LOG, dim_gut(cap(head, CAP_HEAD)))

    def feed_line(self, line):
        m = TS.match(line)
        if m:
            if self.cur_head is not None:
                self.render_record(self.cur_head, self.cur_body)
            self.cur_head, self.cur_body = m.group(1), []
        elif line.strip():
            self.cur_body.append(line)

    # --- rollout (.jsonl) paint: codex's own native session log ---------------------
    # The PARSING lives in plugins/codex/rollout.py (the one owner of the
    # rollout record shapes); these handlers consume its typed records — one
    # handler per record kind, selected via the _RO table below (unknown
    # record types never reach here: the parser returns None for them, as the
    # old ladder fell through silently).

    def _ro_turn_context(self, rec):
        # Model + effort for this turn — shown once (dim ⚙ line) and re-shown
        # only when it changes; the bare model id prices the footer rollup.
        model, eff = rec["model"], rec["effort"]
        tag = model + (" · " + eff if eff else "")
        if model and tag != self.ro_tag:
            self.ro_model, self.ro_tag = model, tag
            O.emit(LOG, dim_gut("⚙ " + tag))

    def _ro_usage(self, rec):
        # Cumulative usage snapshot. Folded into the scoreboard ONCE, at the
        # footer — the totals are cumulative, so summing per-record would
        # double-count.
        self.ro_usage = rec["usage"]

    def _ro_patch(self, rec):
        render_patch(rec)

    def _ro_compact(self, rec):
        # Same ⟳ treatment the substream gives a compact_boundary, so a
        # gap in a codex run's history reads the same way.
        O.emit(LOG, O.gut(R.fg(*O.YELLOW) + "⟳ compacted" + RST, SLOT_RGB))

    def _ro_task_started(self, rec):
        self.ro_active = True
        if self.ro_started is None:
            self.ro_started = rec["at"]

    def _ro_task_complete(self, rec):
        self.ro_active = False
        self.ro_completed = rec["at"] or self.ro_completed
        self.ro_done_wall = time.time()

    def _ro_turn_aborted(self, rec):
        self.ro_active, self.ro_aborted, self.ro_done_wall = False, True, time.time()

    def _ro_prompt(self, rec):
        g = O.new_group(LOG)
        O.emit(LOG, chip("⇢", "prompt", g=g, lk=O.COPY_ALL),
               gutter(cap(rec["text"], CAP_PROMPT), g=g))

    def _ro_reasoning(self, rec):
        g = O.new_group(LOG)
        O.emit(LOG, chip("⋯", "reasoning", g=g, lk=O.COPY_ALL),
               dim_gut(cap(rec["text"], CAP_THINK), g=g))

    def _ro_message(self, rec):
        self.last_msg = rec["text"]
        g = O.new_group(LOG)
        O.emit(LOG, chip("✎", "message", g=g, lk=O.COPY_ALL),
               gutter(cap(rec["text"], CAP_MSG), g=g))

    def _ro_search(self, rec):
        g = O.new_group(LOG)
        O.emit(LOG, chip("⌕", "search", g=g, lk=O.COPY_ALL),
               gutter(cap(rec["query"], CAP_HEAD), g=g))

    def _ro_exec_result(self, rec):
        # The exec output record: surface a FAILED exit prominently (the
        # companion path already does this from its "Command failed" lines).
        if rec["exit"] and rec["exit"] != "0":
            self._emit_exit_chip(rec["exit"])

    def _ro_exec(self, rec):
        g = O.new_group(LOG)
        O.emit(LOG, chip("▶", "cmd", g=g, lk=[["cmd", "⧉cmd"]]),
               O.code(rec["cmd"], g=g))

    _RO = {"turn_context": _ro_turn_context, "usage": _ro_usage,
           "patch": _ro_patch, "compact": _ro_compact,
           "task_started": _ro_task_started, "task_complete": _ro_task_complete,
           "turn_aborted": _ro_turn_aborted, "prompt": _ro_prompt,
           "reasoning": _ro_reasoning, "message": _ro_message,
           "search": _ro_search, "exec": _ro_exec,
           "exec_result": _ro_exec_result}

    def feed_rollout(self, rec):
        h = self._RO.get(rec["kind"])
        if h:
            h(self, rec)


def read_status():
    try:
        with open(JSONF, encoding="utf-8") as fh:
            return (json.load(fh).get("status") or "").strip()
    except Exception:
        return ""


def main(run):
    if not (LOG and LOGFILE):
        return
    start = time.time()
    # Wait for the source to appear (a companion .log lands a beat after its sidecar).
    if not T.wait_for(LOGFILE, start + 15,
                      alive=lambda: not S.parked(LOG)):
        run.end("src-never-appeared")
        return

    # Re-check right before the first emit: SessionEnd may have parked the state
    # DB during the wait above (S.parked — the shared session-alive probe; the
    # codex watcher's own loop polls the same file and would never exit if an
    # emit here resurrected it).
    if S.parked(LOG):
        run.end("state-db-parked (before header)")
        return
    O.emit(LOG, O.rule(), O.label("codex ▶ " + LABEL, SLOT_RGB), O.rule())

    tail = T.FileTailer(LOGFILE)
    rd = Renderer()          # this run's mutable render state (both sources)

    def pump():
        # Loops while a capped read (core/tail.py PUMP_MAX_B) left a backlog —
        # every call site treats one pump() as "caught up" (see substream).
        while True:
            lines = tail.pump()
            for ln in (lines or ()):
                s = ln.decode("utf-8", "replace")
                if not ROLLOUT:
                    rd.feed_line(s)
                    continue
                s = s.strip()
                if s:
                    try:
                        rec = RO.parse(json.loads(s))
                        if rec is not None:
                            rd.feed_rollout(rec)
                    except Exception:
                        # A COMPLETE line (FileTailer only surfaces newline-
                        # terminated lines — mid-write partials stay pending)
                        # that still isn't JSON is genuinely malformed, but a
                        # broken writer could produce thousands: audit the
                        # FIRST one per run in full (A.error), just count the
                        # rest — end() folds the total into the stream_end
                        # reason, so the audit sees ≤1 error row per run.
                        rd.ro_malformed += 1
                        if rd.ro_malformed == 1:
                            A.error(LOG, "codex rollout parse",
                                    {"src": LOGFILE, "offset": tail.consumed,
                                     "line": s[:200]})
            if lines is None or not tail.capped:
                return

    def end(reason):
        # Stream-end wrapper: stamp the malformed-rollout-line count (if any)
        # onto the audited end reason — the once-per-stream summary half of
        # the first-line-only A.error above.
        if rd.ro_malformed:
            reason += " · malformed-lines:%d" % rd.ro_malformed
        run.end(reason)

    # rollout: close the block if no new turn starts within grace. Env override
    # exists solely for the test suite (docs/testing.md).
    GRACE = float(os.environ.get("CLAUDE_CODEX_GRACE_S") or 8.0)
    while True:
        pump()
        if S.parked(LOG):                        # session ended (state DB parked) -> stop
            end("state-db-parked (session end)")
            # No footer: writing it would go into the parked *.keep snapshot via
            # the cached connection — or recreate the DB file outright.
            return
        if ROLLOUT:
            if rd.ro_done_wall and not rd.ro_active and (time.time() - rd.ro_done_wall) >= GRACE:
                pump(); end("task-complete"); break
        elif read_status() in ("completed", "failed", "cancelled"):
            time.sleep(0.2); pump(); pump()  # drain the tail
            end("sidecar-status: " + read_status())
            break
        if time.time() - start > T.BACKSTOP_S:   # backstop for a stuck run
            end("backstop-timeout")
            break
        time.sleep(T.POLL_S)

    if not ROLLOUT and rd.cur_head is not None:
        rd.render_record(rd.cur_head, rd.cur_body)

    if ROLLOUT:
        state = "failed" if rd.ro_aborted else "ended"
        sec = (rd.ro_completed - rd.ro_started) if (rd.ro_started and rd.ro_completed) \
            else max(0.0, time.time() - start)
    else:
        state = "failed" if read_status() == "failed" else "ended"
        sec = max(0.0, time.time() - start)
    dur = O.fmt_dur(sec)
    foot = f"■ codex {LABEL} {state} · {dur}"
    if ROLLOUT and isinstance(rd.ro_usage, dict):
        # Cumulative rollup from the run's last token_count: fresh billed
        # input (input minus cached) / generated output / cache-hit share —
        # the same figures a subagent footer shows, so runs compare at a
        # glance. Folded into the session scoreboard ONCE here (bump-agent —
        # the meta carries agent kind/model + the split, so the Σ row and cost
        # are re-derivable from the audit DB alone). No fold on the parked-DB
        # exit above, and none for companion (.log) runs — their usage isn't
        # in the activity log (their rollout is deliberately not adopted).
        # rollout.usage_split is the ONE total_token_usage mapping (the
        # timeline read model consumes the same figures).
        fresh, tout, tcache, tin = RO.usage_split(rd.ro_usage)
        # Shared footer fragment (core/streamfmt.py) — reads=tin: codex's
        # cumulative input_tokens already includes the cached share.
        foot += SF.tok_rollup(fresh, tout, tcache, reads=tin)
        usd = codex_cost_usd(rd.ro_model, fresh, tout, tcache)
        if usd:
            foot += " · ≈ " + O.fmt_usd(usd)
        deltas = {}
        if usd:
            deltas["cost"] = usd
        if fresh or tout:
            deltas["tokens"] = fresh + tout
        if fresh or tout or tcache:
            # O.split_tokens owns the Σ-row tk_* arithmetic. create=0: codex
            # reports no cache-creation category, and `fresh` is already net of
            # its cache reads, so tk_in == fresh (nothing to subtract).
            deltas.update(O.split_tokens(fresh, tout, tcache, 0))
        if deltas:
            O.bump(LOG, meta={"agent_id": "", "kind": "codex",
                              "model": rd.ro_model, "in": fresh, "out": tout,
                              "cache": tcache, "create": 0, "src": LOGFILE,
                              "label": LABEL}, **deltas)
    O.emit(LOG, O.rule(), O.label(foot, SLOT_RGB), O.rule())


def entry():
    _init(sys.argv)
    with T.stream_lifecycle(LOG, "codex", task_id=LABEL, src_path=LOGFILE,
                            ctx={"src": LOGFILE, "label": LABEL}) as run:
        main(run)
