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
from datetime import datetime

from core import agentblocks as AB
from core import env as EV
from core import ops as O
from core import render as R
from core import state as S
from core import streamfmt as SF
from core import tail as T
from plugins.codex import rollout as RO

A = O.A    # audit trail (real module, or a no-op stub if it failed to import)

RST, FAIL = R.RST, R.fg(*O.RED)

# The `pending_exec` entry KINDS for an open block awaiting its result: a
# non-shell tool call, and a SUBAGENT's shell command (a STANDALONE exec's entry
# carries no kind — it is the historical shape, closed by _exec_close).
_PEND_TOOL, _PEND_CMD = "tool", "cmd"

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

# The three REGISTERS a codex run is painted in — WHO the run is to the session,
# which is the only thing the paint forks on:
#   STANDALONE — a codex host on its OWN. It IS the session's main agent, so it
#       paints like one: no run banner, no ⚙ line, no prose (that comes back as
#       conversation bubbles), commands in Claude's own semantic colours + block
#       shape, file ops as bare `line`s (docs/codex.md *Standalone command parity*).
#   SIDECAR — a run inside a Claude host. Its own bracketed sub-stream in the codex
#       palette, banner and footer included; the historical shape.
#   SUBAGENT — a codex-native child agent of a standalone host. It is a CHILD, so
#       it paints as one: core/agentblocks builds every block, it wears the SUB
#       palette its watcher assigned, and its launch/result cards, notes and stamps
#       are the same ones a Claude subagent's substream writes.
# Selected by the watcher through the env (an explicit flag each — the rollout
# cannot say which role its watcher spawned it for).
REG_STANDALONE, REG_SIDECAR, REG_SUBAGENT = "standalone", "sidecar", "subagent"
REGISTER = REG_SIDECAR


def _init(argv):
    """Bind this run's identity from the shim's argv:
      claude-codex-stream.py MIRROR_LOG "r,g,b" SRCFILE JSONFILE LABEL
    plus the REGISTER, from $CLAUDE_CODEX_STANDALONE / $CLAUDE_CODEX_SUBAGENT (set
    by plugins/codex/watch.spawn — see the REG_* block above)."""
    global LOG, SLOT_RGB, LOGFILE, JSONF, LABEL, ROLLOUT, REGISTER
    LOG      = argv[1] if len(argv) > 1 else ""
    SLOT_RGB = tuple(int(x) for x in argv[2].split(",")) if len(argv) > 2 else (0, 200, 150)
    LOGFILE  = argv[3] if len(argv) > 3 else ""
    JSONF    = argv[4] if len(argv) > 4 else "-"
    LABEL    = argv[5] if len(argv) > 5 else "task"
    ROLLOUT  = LOGFILE.endswith(".jsonl")
    if os.environ.get("CLAUDE_CODEX_SUBAGENT") == "1":
        REGISTER = REG_SUBAGENT
    elif os.environ.get("CLAUDE_CODEX_STANDALONE") == "1":
        REGISTER = REG_STANDALONE
    else:
        REGISTER = REG_SIDECAR


def _iso_ts(s):
    """An ISO-8601 rollout envelope timestamp -> epoch seconds, or None. Tolerant
    of the trailing `Z` codex writes; any unparseable value is None so a missing
    clock degrades to an unknown duration rather than raising."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None

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
CAP_TOOL      = 10  # a non-shell tool call's ARGUMENTS (`tools.web__run({…})`)

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


def render_patch(rec, blocks=None):
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
        # A SUBAGENT's file op is built by the shared child-agent presenter, so it
        # carries the same `who` + model/ctx tags a Claude subagent's Read/Update
        # line does (`blocks` is passed only in that register) — and that builder
        # owns the text too, so only the other registers shape it here.
        if blocks is not None:
            O.emit(LOG, *blocks.file_line(verb, name, rgb, added=f["added"],
                                          removed=f["removed"]))
        else:
            # A STANDALONE run is the session's main agent, so its file op is the
            # LEAD's shape: a bare `line`, exactly what claude_code's file_fmt
            # emits. That is not cosmetic — a `gut` op names no ACTIVITY CLASS, so
            # a standalone run's reads and edits were invisible to the web's item
            # kind and to every view-mode summary (the same fix agent scope makes
            # for a subagent's file ops, dashboard/opshtml/actclass.as_lead). A
            # SIDECAR run keeps the guttered shape: a sub-stream hanging off its
            # own bar.
            line = SF.file_line(verb, name, rgb, added=f["added"],
                                removed=f["removed"])
            O.emit(LOG, O.line(line) if REGISTER == REG_STANDALONE
                   else O.gut(line, SLOT_RGB))
        O.bump(LOG, tool=tool, file=f["path"], added=f["added"], removed=f["removed"])

# A companion job-log line is prefixed with an ISO timestamp; the tail is the event
# head. Un-prefixed lines are continuation body of the preceding block event.
TS = re.compile(r"^\[\d{4}-\d\d-\d\dT[\d:.]+Z\]\s?(.*)$")


# Block shapes shared with the substream renderer (core/streamfmt.py), bound to
# this stream's identity. chip's g + lk tie a block's header to its code/gut body
# for the ⧉ copy handler — a fresh O.new_group() per block (codex records carry
# no tool_use_id). Same affordance the claude-session mirror paints (core/copy.py).
cap = SF.cap


def chip(glyph, kind, g=None, lk=None, bubbled=False, web=False, note=None):
    return SF.chip("codex", glyph, kind, SLOT_RGB, g=g, lk=lk, bubbled=bubbled,
                   web=web, note=note)


def gutter(text, g=None, bubbled=False, web=False):
    return SF.gutter(text, SLOT_RGB, g=g, bubbled=bubbled, web=web)


def dim_gut(text, g=None, bubbled=False, chrome=False):
    return SF.dim_gut(text, SLOT_RGB, g=g, bubbled=bubbled, chrome=chrome)


def _tool_rgb():
    """The colour a NON-shell tool block wears. A STANDALONE run is the session's
    MAIN agent, so its tool call is painted in the SEMANTIC command colour and
    reads as ordinary main-session activity downstream (the quiet ⏺ register, the
    `tool` activity class) — the same rule its exec blocks follow (docs/codex.md
    *Standalone command parity*). A SIDECAR run keeps the codex palette: there the
    run is a sub-stream among the host's own work and the colour is what says so.
    (A SUBAGENT's tool block is built by core/agentblocks in its own SUB-palette
    colour and never asks this.)"""
    return SF.CMD_OK if REGISTER == REG_STANDALONE else SLOT_RGB


# Rollout kinds the mirror renderer deliberately does NOT paint (yet). Every
# rollout.KINDS member is EITHER handled by Renderer._RO below OR listed here —
# the both-directions drift contract in tests/test_l1f_codex_rollout.py fails
# until a new/renamed parser kind is decided one way or the other
# (docs/codex.md *Kind drift contract*). This set is documentation + the
# contract's ignore-side; feed_rollout does not consult it (it already ignores
# any kind absent from _RO), so listing a kind here changes no paint behaviour.
IGNORE_KINDS = frozenset({
    "chat",              # prose register — rendered via plugins.conversation() bubbles (P3), not ops
    "think",             # prose register — rendered via plugins.conversation() bubbles (P3), not ops
    "stdin",             # backgrounded-exec continuation — rich rendering pending (P2)
    "patch_call",        # patch lifecycle detail — patch (patch_apply_end) already renders the file lines (P2/P5)
    "compact_boundary",  # top-level compaction boundary — the event_msg `compact` already paints ⟳
    "ask",               # request_user_input dialog — surfaced via the web question card, not the mirror
    "plan",              # plan-mode Plan item — surfaced via the web plan card + transcript, not the mirror
    "settings",          # thread_settings_applied — the picker's model/effort state, read by ctx/effort, not painted
    "bad",               # malformed/undecodable line — counted in the malformed audit, never painted
})


# The record kinds whose paint OPENS a new block, and which therefore commit a
# SUBAGENT's buffered message first (see Renderer.flush_msg). Deliberately not
# "every kind": a `usage`/`turn_context`/`task_started` record paints nothing, and
# flushing on one would turn the run's FINAL message — its ⇠ result card — into an
# ordinary ✎ message, since a token_count always trails the last message.
_FLUSH_BEFORE = frozenset({"exec", "tool", "patch", "search", "prompt",
                           "compact", "reasoning"})


class Renderer:
    """Per-run mutable render state for BOTH sources (companion + rollout) —
    was ~10 module globals mutated via `global` in render_record/feed_rollout;
    gathering them here matches the substream_render.py house shape (a
    state-holding class the lifecycle instantiates per run)."""

    def __init__(self):
        # SUBAGENT register only: the shared CHILD-AGENT presenter (core/
        # agentblocks.py) — the same builders (and the same stamp policy) the
        # Claude substream paints its agent with, bound to THIS run's identity.
        # `tags` is the model·effort chip turn_context keeps current, so it rides
        # every block header instead of the sidecar's separate ⚙ line; `agent_dur`
        # times the run from its bootstrap task_started for the finish note.
        self.blocks = AB.AgentStream(label=LABEL, rgb=SLOT_RGB,
                                     register=AB.REG_AGENT,
                                     tags=lambda: self.ro_tag,
                                     agent_dur=self._agent_dur)
        # …and its buffered assistant message: the LAST message a child sends
        # before it completes is its RESULT (the ⇠ card), so a message is held
        # until either the next block paints (making it an intermediate ✎ message)
        # or the task completes. Exactly the substream's flush_msg discipline.
        self.pending_msg = None
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
        # An OPEN block awaiting its result, keyed by call_id (codex returns every
        # custom-tool output through the same `custom_tool_call_output`, which
        # carries no tool name — the call_id is the only pairing there is). Two
        # kinds live here: a STANDALONE exec command's block (header out now,
        # output + finish chip when the result lands — the Claude live-block
        # split), and — in BOTH registers — a `· <name>` TOOL block, whose answer
        # must land behind the same ⧉ click as its request.
        self.pending_exec = {}
        # The query of the search painted last, and only while it is still the
        # last thing painted — see _ro_search's two-register note.
        self.ro_last_search = None
        # STANDALONE only: the cumulative token totals already folded into the
        # scoreboard. A standalone stream tails the WHOLE session (never ends on a
        # per-turn grace), so tokens fold INCREMENTALLY — each token_count's DELTA
        # over these — instead of once at a footer that would never come; live,
        # like the OTLP receiver feeds a Claude session.
        self.f_fresh = self.f_out = self.f_cache = 0
        # SUBAGENT rollout only: the fork epoch (RO.subagent_fork_epoch, set by
        # main()) and a forward gate. A subagent rollout OPENS with a burst
        # replaying the PARENT thread; those records are DROPPED until the child's
        # own bootstrap task_started passes (docs/codex.md *Sidecar → subagent
        # parity*). None fork_epoch => not a subagent => open from the first record.
        self.fork_epoch = None
        self.sub_open = True

    # --- SUBAGENT register: the child-agent lifecycle ---------------------------

    def _agent_dur(self):
        """How long this child has been running, for the finish note — from its
        BOOTSTRAP task_started (`ro_started`, stamped at the gate flip below,
        because the bootstrap record itself is dropped as turn chrome) to its
        completion, or to now while it still runs. "" when the start is unknown,
        and the note then simply carries no duration."""
        if not self.ro_started:
            return ""
        end = self.ro_completed or time.time()
        return O.fmt_dur(max(0.0, end - self.ro_started))

    def _emit_launch(self, rec):
        """The child's LAUNCH CARD, emitted once, at the moment its own turns
        begin (the bootstrap-gate flip). The brief behind the click comes from the
        replayed-parent prefix this gate is dropping — the only plaintext
        statement of the task there is (plugins/codex/rollout.subagent_brief)."""
        self.ro_started = self.ro_started or rec.get("at")
        brief = cap(RO.subagent_brief(LOGFILE), CAP_PROMPT)
        if not brief:
            return          # a card with nothing behind it is not a card
        O.emit(LOG, *self.blocks.launch(brief, O.new_group(LOG)))

    def flush_msg(self, is_result=False):
        """Commit the buffered assistant message — as the ⇠ RESULT card when the
        run is completing, else as an intermediate ✎ message."""
        if self.pending_msg is None:
            return
        text, self.pending_msg = self.pending_msg, None
        build = self.blocks.result if is_result else self.blocks.message
        O.emit(LOG, *build(text, O.new_group(LOG)))

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
        # Model + effort for this turn — tracked always (the bare model id prices
        # the footer/fold), but the dim `⚙ model · effort` line is codex-specific
        # chrome a STANDALONE (main-agent) mirror must NOT show — a Claude mirror
        # has no per-turn model line, and the model/effort live on the web
        # scoreboard + ctx bar instead. A SIDECAR run still shows it (its own
        # sub-stream). Same rule as the run banner + the prose register.
        model, eff = rec["model"], rec["effort"]
        tag = model + (" · " + eff if eff else "")
        if model and tag != self.ro_tag:
            self.ro_model, self.ro_tag = model, tag
            # A SUBAGENT shows the same fact the way a Claude subagent does — as
            # the model/effort TAG on every block header (the AgentStream reads
            # `ro_tag` above) — so it paints no line of its own here either.
            if REGISTER == REG_SIDECAR:
                # chrome=1: the line is this run's own frame (core/ops.py's
                # `chrome`) — the terminal paints it, every web view drops it,
                # where the model belongs on the run's card instead. Structural
                # now; opshtml/actclass.codex_chrome still sniffs the text for
                # the ops already on disk, which no restart can re-stamp.
                O.emit(LOG, dim_gut("⚙ " + tag, chrome=True))

    def _fold_bump(self, fresh, tout, tcache):
        # The attributed codex scoreboard fold — the ONE place the token/cost
        # deltas are bumped (kind=codex meta, so the Σ row + cost are re-derivable
        # from the audit alone). Shared by the sidecar footer (folds the cumulative
        # total ONCE) and the standalone incremental path (folds each DELTA).
        # O.split_tokens owns the Σ-row tk_* arithmetic; create=0 (codex reports no
        # cache-creation category, and `fresh` is already net of its cache reads).
        deltas = {}
        usd = codex_cost_usd(self.ro_model, fresh, tout, tcache)
        if usd:
            deltas["cost"] = usd
        if fresh or tout:
            deltas["tokens"] = fresh + tout
        if fresh or tout or tcache:
            deltas.update(O.split_tokens(fresh, tout, tcache, 0))
        if deltas:
            O.bump(LOG, meta={"agent_id": "", "kind": "codex",
                              "model": self.ro_model, "in": fresh, "out": tout,
                              "cache": tcache, "create": 0, "src": LOGFILE,
                              "label": LABEL}, **deltas)

    def _ro_usage(self, rec):
        # Cumulative usage snapshot. The SIDECAR folds it ONCE at the footer (the
        # totals are cumulative, so summing per-record would double-count).
        self.ro_usage = rec["usage"]
        if REGISTER == REG_STANDALONE:
            # A standalone stream has no per-turn footer, so fold the DELTA over
            # what's already folded — the cumulative total keeps rising across
            # turns, and only the increment is new spend.
            fresh, tout, tcache, _tin = RO.usage_split(rec["usage"])
            df, do, dc = (fresh - self.f_fresh, tout - self.f_out,
                          tcache - self.f_cache)
            if df > 0 or do > 0 or dc > 0:
                self._fold_bump(max(0, df), max(0, do), max(0, dc))
                self.f_fresh, self.f_out, self.f_cache = fresh, tout, tcache

    def _ro_patch(self, rec):
        render_patch(rec, self.blocks if REGISTER == REG_SUBAGENT else None)

    def _ro_compact(self, rec):
        # Same ⟳ treatment the substream gives a compact_boundary, so a
        # gap in a codex run's history reads the same way — literally the same
        # builder in the SUBAGENT register (codex's record names no token
        # figures, so the line is the bare mark).
        if REGISTER == REG_SUBAGENT:
            O.emit(LOG, *self.blocks.compact())
            return
        O.emit(LOG, O.gut(R.fg(*O.YELLOW) + "⟳ compacted" + RST, SLOT_RGB))

    def _ro_task_started(self, rec):
        self.ro_active = True
        if self.ro_started is None:
            self.ro_started = rec["at"]

    def _ro_task_complete(self, rec):
        self.ro_active = False
        self.ro_completed = rec["at"] or self.ro_completed
        self.ro_done_wall = time.time()
        # …and the message the child was holding is its RESULT: the ⇠ card, whose
        # note carries the duration this record just closed (set above, so
        # _agent_dur reports the real task time and not "still running").
        if REGISTER == REG_SUBAGENT:
            self.flush_msg(is_result=True)

    def _ro_turn_aborted(self, rec):
        self.ro_active, self.ro_aborted, self.ro_done_wall = False, True, time.time()

    # The CONVERSATION register (prompt / assistant message / reasoning). A
    # STANDALONE codex host is the session's MAIN agent, and the terminal mirror
    # shows a main agent's ACTIVITY only — never its conversation — exactly as a
    # Claude session's mirror does (the prose lives in the TUI, and on the web it
    # comes from plugins.conversation as bubbles, NOT from these ops, which
    # op_items already drops for a codex lead). So a standalone run emits no prose
    # into the mirror. A SIDECAR run (codex inside a Claude host) still emits it —
    # there the run reads as its own bracketed sub-stream (pending the subagent
    # abstraction, docs/codex.md *Sidecar → subagent parity*).
    # bubbled=True on the three PROSE emitters below: a SIDECAR run's prose is
    # re-bubbled from its rollout by plugins.conversation (docs/codex.md *Sidecar →
    # subagent parity*), so agent scope drops the op — the unified prose-drop signal
    # (core/ops.py "bubbled"), replacing the codexprose: scope marker. Reached ONLY
    # for a sidecar (each returns early when STANDALONE); a companion .log run never
    # reaches these (render_record paints it, no rollout to re-bubble → no bubbled).
    def _ro_prompt(self, rec):
        if REGISTER == REG_STANDALONE:
            return
        if REGISTER == REG_SUBAGENT:
            # A child's LAUNCH card is emitted at the bootstrap gate (_emit_launch)
            # from the brief; a prompt reaching it mid-run is a FOLLOW-UP task
            # (codex's `followup_task`), which is prose, not a second launch.
            O.emit(LOG, *self.blocks.prompt(cap(rec["text"], CAP_PROMPT),
                                            O.new_group(LOG)))
            return
        # web=True + a `Codex "<label>" ran` note: the ⇢ prompt is the codex run's
        # LAUNCH card in the LEAD mirror (in_scope keeps a web-stamped op there),
        # the exact analog of a Claude subagent's ⇢ prompt (substream render_prompt)
        # — so the lead has a foldable ACT_CODEX card ("ran N codex runs") instead
        # of only bubbles, and view modes fold/expand it. bubbled=True still drops
        # it in the run's OWN scope (its conversation re-bubbles the prompt there).
        g = O.new_group(LOG)
        O.emit(LOG, chip("⇢", "prompt", g=g, lk=O.COPY_ALL, bubbled=True, web=True,
                         note=SF.codex_note(LABEL, "ran")),
               gutter(cap(rec["text"], CAP_PROMPT), g=g, bubbled=True, web=True))

    def _ro_reasoning(self, rec):
        if REGISTER == REG_STANDALONE:
            return
        if REGISTER == REG_SUBAGENT:
            O.emit(LOG, *self.blocks.reasoning(cap(rec["text"], CAP_THINK),
                                               O.new_group(LOG)))
            return
        g = O.new_group(LOG)
        O.emit(LOG, chip("⋯", "reasoning", g=g, lk=O.COPY_ALL, bubbled=True),
               dim_gut(cap(rec["text"], CAP_THINK), g=g, bubbled=True))

    def _ro_message(self, rec):
        self.last_msg = rec["text"]
        if REGISTER == REG_STANDALONE:
            return
        if REGISTER == REG_SUBAGENT:
            # BUFFERED, not painted: which card this message becomes is only known
            # once something follows it (see flush_msg). UNCAPPED, like the
            # substream's — a child's message and its result are what the stream
            # exists to deliver.
            self.flush_msg()                  # commit the previous one
            self.pending_msg = rec["text"]
            return
        g = O.new_group(LOG)
        O.emit(LOG, chip("✎", "message", g=g, lk=O.COPY_ALL, bubbled=True),
               gutter(cap(rec["text"], CAP_MSG), g=g, bubbled=True))

    def _ro_search(self, rec):
        # ONE search can reach here TWICE: the parser answers `search` from BOTH
        # of its registers (the event_msg one is all cli 0.146 writes, but the
        # response_item twin still parses), so a build that emits both would
        # paint the block twice. An IMMEDIATELY repeated query
        # is therefore collapsed: `ro_last_search` is cleared by the next record
        # of any other kind (feed_rollout), so a genuine second search for the
        # same words later still gets its own block. The de-dup lives here and
        # not in the parser, which must keep reporting what the file says.
        if rec["query"] and rec["query"] == self.ro_last_search:
            return
        self.ro_last_search = rec["query"]
        if REGISTER == REG_SUBAGENT:
            # A child's search is one of its TOOL CALLS — painted as the shared
            # `· <name>` block with the query behind the click, exactly as a
            # Claude agent's WebSearch is (substream `_use_other`). The ⌕ glyph is
            # codex's own vocabulary and has no counterpart there.
            O.emit(LOG, *self.blocks.tool_open("search",
                                               cap(rec["query"], CAP_HEAD),
                                               O.new_group(LOG)))
            return
        g = O.new_group(LOG)
        O.emit(LOG, chip("⌕", "search", g=g, lk=O.COPY_ALL),
               gutter(cap(rec["query"], CAP_HEAD), g=g))

    def _ro_tool(self, rec):
        """A NON-shell tool call — `tools.web__run({…})` and friends, which codex
        ≥ 0.146 runs through the very same `exec` custom tool as a shell command
        (plugins/codex/rollout.js_tool_call). Painted as the QUIET `· <name>`
        block a generic tool call gets everywhere else in this repo
        (core/agentblocks.TOOL_GLYPH — the substream paints an agent's ToolSearch
        exactly so), with the ARGUMENTS behind the click and the answer appended
        when the result lands.

        This is deliberately MORE than the Claude LEAD shows: Claude's hooks
        paint no generic tool block at all (only Bash / files / monitors / skills
        / mail), so a standalone codex host is the first main agent whose web and
        MCP calls are visible. Hiding them for symmetry's sake would be the same
        mistake by another route — the previous behaviour laundered these into a
        `▶ cmd` block of raw JavaScript, which is how a subagent's entire real
        work came to read as gibberish."""
        g = O.new_group(LOG)
        args = cap(rec["args"], CAP_TOOL)
        name = rec["name"] or "tool"
        if REGISTER == REG_SUBAGENT:
            # …and in the SUBAGENT register it is literally the block a Claude
            # agent's generic tool call gets — same builder, same stamps.
            O.emit(LOG, *self.blocks.tool_open(name, args, g))
            self.pending_exec[rec["call_id"]] = {"kind": _PEND_TOOL, "gid": g}
            return
        col = _tool_rgb()
        # STANDALONE: a bare label in the semantic colour (the lead's own shape —
        # no `who`, no palette). SIDECAR: the run's identity chip, as its other
        # blocks wear.
        head = (O.label(AB.TOOL_GLYPH + " " + name, col, g=g, lk=O.COPY_ALL)
                if REGISTER == REG_STANDALONE
                else chip(AB.TOOL_GLYPH, name, g=g, lk=O.COPY_ALL))
        O.emit(LOG, head, *([SF.gutter(args, col, g=g)] if args else []))
        self.pending_exec[rec["call_id"]] = {"kind": _PEND_TOOL, "gid": g}

    def _tool_close(self, rec, pend):
        """…and that tool call's ANSWER, behind the same ⧉ group as its request.
        A non-zero exit adds the shared red failure mark (core/agentblocks.
        fail_text) under it — the same words a Claude agent's failed tool result
        wears, so the two read alike."""
        out = (rec["output"] or "").rstrip("\n")
        failed = bool(rec["exit"]) and rec["exit"] != "0"
        if REGISTER == REG_SUBAGENT:
            O.emit(LOG, *self.blocks.tool_close(pend["gid"],
                                                cap(out, CAP_OUTPUT),
                                                failed=failed))
            return
        col = _tool_rgb()
        body = R.emphasize(R.unescape(cap(out, CAP_OUTPUT))) if out.strip() \
            else SF.no_output_body()
        ops = [O.gut(body, col, g=pend["gid"])]
        if failed:
            ops.append(O.gut(FAIL + AB.fail_text(rec["exit"]) + RST, col,
                             g=pend["gid"]))
        O.emit(LOG, *ops)

    def _ro_exec_result(self, rec):
        # A tool block's output closes that block, in BOTH registers — its
        # request is already on screen and its answer belongs behind the same
        # click (checked FIRST: the output record itself says only "some custom
        # tool with this call_id finished", so the open block is what names it).
        pend = self.pending_exec.get(rec["call_id"])
        if pend and pend.get("kind") == _PEND_TOOL:
            self._tool_close(rec, self.pending_exec.pop(rec["call_id"]))
            return
        if pend and pend.get("kind") == _PEND_CMD:
            # a SUBAGENT's command block: body + outcome, the shared builder's
            # (an exit code names itself in the failure mark — the one thing a
            # codex result carries that a Claude tool_result does not)
            self.pending_exec.pop(rec["call_id"], None)
            out = (rec["output"] or "").rstrip("\n")
            failed = bool(rec["exit"]) and rec["exit"] != "0"
            O.emit(LOG, *self.blocks.cmd_close(
                pend["gid"], cap(out, CAP_OUTPUT), failed=failed,
                exit_code=rec["exit"] if failed else None))
            return
        if REGISTER == REG_STANDALONE:
            self._exec_close(rec)
            return
        # In-a-Claude-session (not yet folded into the subagent abstraction —
        # docs/codex.md): surface only a FAILED exit prominently, as the companion
        # path does from its "Command failed" lines.
        if rec["exit"] and rec["exit"] != "0":
            self._emit_exit_chip(rec["exit"])

    def _ro_exec(self, rec):
        if REGISTER == REG_SUBAGENT:
            # A child's shell command is the shared `▶ foreground` block, opened
            # now and closed by its result — a Claude subagent's plain fg command
            # exactly. Deliberately NO per-command elapsed chip: the standalone
            # register times its blocks because it is a main agent's mirror, and a
            # child's stream matches the Claude child instead (parity over a
            # codex-only nicety).
            g = O.new_group(LOG)
            self.pending_exec[rec["call_id"]] = {"kind": _PEND_CMD, "gid": g}
            O.emit(LOG, *self.blocks.cmd_open(rec["cmd"], g))
            return
        if REGISTER == REG_STANDALONE:
            # A standalone codex host IS the main agent, so its command is painted
            # EXACTLY as Claude's foreground block — the shared core/streamfmt
            # opener, in the semantic command colours (no codex palette), opened NOW
            # so a long-running command shows the instant it starts. _exec_close
            # appends the output + the shared finish chip when the result lands.
            # Because the block wears a semantic colour, the web classifier reads it
            # as ordinary command activity (ACT_BASH) rather than folding it into
            # "ran N codex runs" (dashboard/opshtml/actclass.py).
            gid = O.new_group(LOG)
            self.pending_exec[rec["call_id"]] = {
                "cmd": rec["cmd"], "ts": rec.get("ts"), "gid": gid}
            O.emit(LOG, *SF.command_open(rec["cmd"], gid))
            return
        g = O.new_group(LOG)
        O.emit(LOG, chip("▶", "cmd", g=g, lk=[["cmd", "⧉cmd"]]),
               O.code(rec["cmd"], g=g))

    def _exec_close(self, rec):
        """STANDALONE: close the foreground command block _ro_exec opened, matched
        to its exec by call_id — the output behind the outcome-coloured gutter + the
        shared finish chip. An ORPHAN result (a backgrounded write_stdin poll's
        output, whose call_id is the stdin call's, not the exec's) has no open block
        to close, so — as in the sidecar path — only its failed exit is surfaced."""
        pend = self.pending_exec.pop(rec["call_id"], None)
        failed = bool(rec["exit"]) and rec["exit"] != "0"
        if not pend:
            if failed:
                self._emit_exit_chip(rec["exit"])
            return
        out = (rec["output"] or "").rstrip("\n")
        body = R.emphasize(R.unescape(cap(out, CAP_OUTPUT))) if out.strip() \
            else SF.no_output_body()
        a, b = _iso_ts(pend.get("ts")), _iso_ts(rec.get("ts"))
        dur = O.fmt_dur(b - a) if (a is not None and b is not None and b >= a) else "?"
        chip_txt, col = SF.finish_chip(dur, failed=failed,
                                       exit_code=rec["exit"] if failed else None)
        O.emit(LOG, *SF.command_close(body, chip_txt, col, pend["gid"]))

    _RO = {"turn_context": _ro_turn_context, "usage": _ro_usage,
           "patch": _ro_patch, "compact": _ro_compact,
           "task_started": _ro_task_started, "task_complete": _ro_task_complete,
           "turn_aborted": _ro_turn_aborted, "prompt": _ro_prompt,
           "reasoning": _ro_reasoning, "message": _ro_message,
           "search": _ro_search, "exec": _ro_exec, "tool": _ro_tool,
           "exec_result": _ro_exec_result}

    def feed_rollout(self, rec):
        if not self.sub_open:
            # still in a subagent rollout's replayed-parent PREFIX — drop every
            # record until the child's own bootstrap task_started passes (that
            # task_started is turn chrome, dropped too; the child's turns start on
            # the NEXT record). Race-safe: each record self-decides as it arrives.
            if RO.is_child_bootstrap(rec, self.fork_epoch):
                self.sub_open = True
                if REGISTER == REG_SUBAGENT:
                    self._emit_launch(rec)     # the ⇢ card, once, right here
            return
        if REGISTER == REG_SUBAGENT and rec["kind"] in _FLUSH_BEFORE:
            # a block is about to paint, so the held message is an INTERMEDIATE
            # one (see flush_msg) — commit it before the block lands under it
            self.flush_msg()
        h = self._RO.get(rec["kind"])
        if h:
            h(self, rec)
        if rec["kind"] != "search":
            # the two-register search de-dup only spans ADJACENT records
            # (_ro_search) — anything else in between ends the run
            self.ro_last_search = None


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
    # The `codex ▶ <label>` run banner is codex-specific chrome — a STANDALONE
    # (main-agent) mirror shows none of it, exactly like a Claude session (whose
    # mirror opens straight into activity). A SIDECAR run keeps the banner: it
    # brackets that run's own sub-stream among the host's other activity.
    # chrome=1 on the banner (and on the footer at the end of main): host
    # scaffolding around this run's stream — see core/ops.py's `chrome` and the
    # ⚙ line above. The bracketing rules carry no flag because they need none:
    # op_items drops every rule/blank before it looks at anything else.
    # Only a SIDECAR paints it: a STANDALONE run is the session itself, and a
    # SUBAGENT opens on its LAUNCH CARD instead — the same way a Claude subagent's
    # stream does, where the host's own header is the (chrome) launch line.
    if REGISTER == REG_SIDECAR:
        O.emit(LOG, O.rule(), O.label("codex ▶ " + LABEL, SLOT_RGB, chrome=True),
               O.rule())

    tail = T.FileTailer(LOGFILE)
    rd = Renderer()          # this run's mutable render state (both sources)
    # A SUBAGENT rollout replays the parent thread's history first; gate it out so
    # the subagent's stream is its OWN turns only (docs/codex.md *Sidecar → subagent
    # parity*). Non-subagent rollout / companion .log => fork_epoch None => no gate.
    if ROLLOUT:
        rd.fork_epoch = RO.subagent_fork_epoch(LOGFILE)
        rd.sub_open = rd.fork_epoch is None

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
    GRACE = EV.env_float("CLAUDE_CODEX_GRACE_S", 8.0)
    while True:
        pump()
        if S.parked(LOG):                        # session ended (state DB parked) -> stop
            end("state-db-parked (session end)")
            # No footer: writing it would go into the parked *.keep snapshot via
            # the cached connection — or recreate the DB file outright. A
            # standalone run's tokens are already folded incrementally (_ro_usage).
            return
        if ROLLOUT:
            # A STANDALONE codex host's rollout IS the whole session — it must
            # stream EVERY turn, like a Claude session's mirror, so it NEVER ends
            # on a per-turn grace (nor the stuck-run backstop): only session end
            # (the parked DB above) stops it. Ending on task-complete froze the
            # mirror after the first idle gap — later turns went unstreamed
            # (docs/codex.md *Standalone streams the whole session*). The
            # per-task grace + footer are for a discrete TASK — a SIDECAR run and
            # a SUBAGENT alike (a child agent ends when its task completes, and
            # its footer is what says so).
            if (REGISTER != REG_STANDALONE and rd.ro_done_wall and not rd.ro_active
                    and (time.time() - rd.ro_done_wall) >= GRACE):
                pump(); end("task-complete"); break
        elif read_status() in ("completed", "failed", "cancelled"):
            time.sleep(0.2); pump(); pump()  # drain the tail
            end("sidecar-status: " + read_status())
            break
        if REGISTER != REG_STANDALONE and time.time() - start > T.BACKSTOP_S:
            end("backstop-timeout")
            break
        time.sleep(T.POLL_S)

    if not ROLLOUT and rd.cur_head is not None:
        rd.render_record(rd.cur_head, rd.cur_body)

    # Only a SIDECAR or SUBAGENT run reaches here — a standalone run returns at
    # the parked exit above (no per-task footer, tokens folded incrementally).
    if ROLLOUT:
        state = "failed" if rd.ro_aborted else "ended"
        sec = (rd.ro_completed - rd.ro_started) if (rd.ro_started and rd.ro_completed) \
            else max(0.0, time.time() - start)
    else:
        state = "failed" if read_status() == "failed" else "ended"
        sec = max(0.0, time.time() - start)
    dur = O.fmt_dur(sec)
    # A SUBAGENT that ended without a task_complete (aborted, backstopped) still
    # owes its held message — as the RESULT, since nothing more is coming.
    if REGISTER == REG_SUBAGENT:
        rd.flush_msg(is_result=True)
    foot = ""
    if ROLLOUT and isinstance(rd.ro_usage, dict):
        # Cumulative rollup from the run's last token_count: fresh billed
        # input (input minus cached) / generated output / cache-hit share —
        # the same figures a subagent footer shows, so runs compare at a
        # glance. Folded into the session scoreboard ONCE here (the shared
        # _fold_bump — bump-agent, the meta carries agent kind/model + the
        # split, so the Σ row and cost are re-derivable from the audit DB
        # alone). None for companion (.log) runs — their usage isn't in the
        # activity log. rollout.usage_split is the ONE total_token_usage mapping.
        fresh, tout, tcache, tin = RO.usage_split(rd.ro_usage)
        # Shared footer fragment (core/streamfmt.py) — reads=tin: codex's
        # cumulative input_tokens already includes the cached share.
        foot += SF.tok_rollup(fresh, tout, tcache, reads=tin)
        usd = codex_cost_usd(rd.ro_model, fresh, tout, tcache)
        if usd:
            foot += " · ≈ " + O.fmt_usd(usd)
        rd._fold_bump(fresh, tout, tcache)
    if REGISTER == REG_SUBAGENT:
        # A CHILD's footer is the child-agent one — `■ <label> ended · <dur>` +
        # this run's rollup tail, the same line a Claude subagent closes on. NOT
        # chrome: it is the agent's own last line, not the host's frame around it.
        O.emit(LOG, *rd.blocks.footer(state, dur, foot))
    else:
        O.emit(LOG, O.rule(),
               O.label(f"■ codex {LABEL} {state} · {dur}" + foot, SLOT_RGB,
                       chrome=True),
               O.rule())


def entry():
    _init(sys.argv)
    with T.stream_lifecycle(LOG, "codex", task_id=LABEL, src_path=LOGFILE,
                            ctx={"src": LOGFILE, "label": LABEL}) as run:
        main(run)
