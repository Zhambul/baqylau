# plugins/claude_code/substream.py — subagent/teammate transcript streamer
# Entry point: claude-substream.py (a thin shim — the entry FILENAME is the audit vocabulary).
# claude-substream.py AGENT_ID TRANSCRIPT_PATH MIRROR_LOG SLOT AGENT_TYPE [PALETTE]
#
# Detached streamer for a SUBAGENT (Task/Agent tool). A subagent fires real hooks
# for each tool it runs, but those alone can't show its *messages* (assistant text)
# or keep messages/commands/results in order. Its full transcript can, though:
# `<dir>/<session>/subagents/agent-<id>.jsonl` records — in order — the prompt, the
# subagent's text messages, every tool_use, and every tool_result. So this process
# (spawned by the SubagentStart hook) tails that transcript and renders all of it
# into the mirror in the subagent's colour, giving full visibility.
#
# Division of labour: the SubagentStart hook claims the colour slot and writes the
# "▶ <type> · <desc>" header; this streamer writes everything below it (prompt,
# messages, commands+output, file ops, the final result) and the "■ <type> ended"
# footer, then releases the slot. A subagent's BACKGROUND command / monitor is
# streamed by claude-stream.py with a DOUBLE gutter (outer = this subagent's
# colour, inner = the job's own palette slot) so nested parallel jobs stay distinct.
#
# This module owns the LIFECYCLE: the argv/env contract, model/effort/ctx
# resolution, nested-tailer spawning, resume checkpointing, the four cancellation
# signals, and the ended-footer. Turning transcript records into paint ops lives
# in substream_render.py (the Renderer instance `REN`).
import json, os, subprocess, sys, time

from core import ops as O
from core import render as R
from core import slots as claude_slots
from core import state as S
from core import streamfmt as SF
from core import tail as T
from plugins.claude_code import accounting as ACC
from plugins.claude_code import hookkit as HK
from plugins.claude_code import model as M
from plugins.claude_code import stream as ST
from plugins.claude_code import substream_render as SR

A = O.A    # audit trail (real module, or a no-op stub if it failed to import)

# --- run identity (argv/env contract) --------------------------------------------
# All of this used to be parsed at module top level. It now lives in _init(),
# called from entry(), so IMPORTING this module (tests, tooling) reads no argv,
# opens no files, and resolves nothing — only running it does. The placeholders
# below just name the module globals every function reads at call time.
AGENT = TPATH = LOG = ATYPE = PALETTE = DESC = LABEL = ""
SLOT = 0
SUB_RGB = (0, 0, 0)
SUB_FG = True
META = {}
DEF_TYPE = AGENT_DEF_FILE = AGENT_DEF_MODEL = SETTINGS_MODEL = SESSION_MODEL = None
EFFORT_CFG = None
BASE = SUBDIR = JSONL = META_PATH = ""
STATE_KEY = USAGE_KEY = BILLED_KEY = ""
REN = None

RST  = R.RST
from core.paths import BIN  # bin/, where the sibling ENTRY scripts live


def _init(argv):
    """Bind this run's identity from the shim's argv:
      claude-substream.py AGENT_ID TRANSCRIPT_PATH MIRROR_LOG SLOT AGENT_TYPE [PALETTE] [DESC]
    plus everything derived from it (meta.json read, model/effort resolution,
    slot colour, the Renderer)."""
    global AGENT, TPATH, LOG, SLOT, ATYPE, PALETTE, DESC, LABEL, SUB_RGB, SUB_FG
    global META, DEF_TYPE, AGENT_DEF_FILE, AGENT_DEF_MODEL, SETTINGS_MODEL
    global SESSION_MODEL, EFFORT_CFG, BASE, SUBDIR, JSONL, META_PATH
    global STATE_KEY, USAGE_KEY, BILLED_KEY, REN
    AGENT   = argv[1]
    TPATH   = argv[2]
    LOG     = argv[3]
    SLOT    = int(argv[4])
    ATYPE   = argv[5] if len(argv) > 5 else "agent"
    # Which palette to colour this block with. An in-process agent-team TEAMMATE rides
    # the very same machinery as an ordinary subagent (same "sub" slot + sub.* markers,
    # same transcript layout) — only the colour family differs, so it's "team" instead
    # of "sub". Everything else (slot index, completion sentinel, footer) is identical.
    PALETTE = argv[6] if len(argv) > 6 else "sub"
    # The task description (from the PreToolUse payload), passed through by the start hook.
    DESC    = argv[7] if len(argv) > 7 else ""
    # What each per-operation line labels this agent with. "general-purpose" is a
    # meaningless catch-all in the mirror, so for it we substitute the task description
    # (e.g. "Get Bali weather") when one is known; every other type keeps its own name,
    # and the header ("▶ general-purpose · <desc>") is untouched — this is the body only.
    LABEL = DESC if (ATYPE == "general-purpose" and DESC) else ATYPE

    SUB_RGB = claude_slots.color(PALETTE, SLOT)
    # Live-stream this subagent's FOREGROUND commands (tee'd by claude-cmd-pre.py's
    # PreToolUse), the same way its background/monitor jobs already stream. On by
    # default; CLAUDE_MIRROR_LIVE_FG_SUB=0 (or the parent CLAUDE_MIRROR_LIVE_FG=0)
    # opts out, matching claude-cmd-pre.py's gate so the two agree on when a marker exists.
    SUB_FG = (os.environ.get("CLAUDE_MIRROR_LIVE_FG_SUB", "1") != "0"
              and os.environ.get("CLAUDE_MIRROR_LIVE_FG", "1") != "0")

    # Model / effort / context-window resolution lives in model.py (this package); this block
    # just binds it to THIS agent's identity (its meta.json, definition file, and the
    # parent session's transcript).
    META = M.agent_meta(TPATH, AGENT)
    # Look up the definition by its real name (customAgentType) — the short agentType a
    # teammate reports ("container") won't match the def's `name:`/filename ("task-container").
    DEF_TYPE = META.get("customAgentType") or ATYPE
    AGENT_DEF_FILE  = M.agent_def_file(DEF_TYPE)
    AGENT_DEF_MODEL = M.def_field(AGENT_DEF_FILE, "model")
    SETTINGS_MODEL  = M.settings_field("model")
    SESSION_MODEL   = M.session_model(TPATH)
    EFFORT_CFG      = M.effort_config(AGENT_DEF_FILE)

    # Where the subagent's transcript lives. The completion signal and the resume
    # checkpoint live on this agent's record in the per-session state DB
    # (core.state agents table — was sub.done.* / sub.pos.* files in the .slots dir):
    #   done      — set to 1 by the SubagentStop hook; this streamer polls it.
    #   pos       — byte offset of the last fully consumed transcript line. An idle
    #               TEAMMATE fires SubagentStop (this streamer finalises and dies) and a
    #               later message fires a fresh SubagentStart, which spawns a NEW streamer
    #               for the SAME transcript — without the checkpoint that streamer would
    #               re-render the whole history. Deliberately NOT cleared in cleanup():
    #               it must outlive the streamer to make the resume seamless.
    BASE = TPATH[:-6] if TPATH.endswith(".jsonl") else TPATH
    SUBDIR = os.path.join(BASE, "subagents")
    JSONL  = os.path.join(SUBDIR, f"agent-{AGENT}.jsonl")
    META_PATH = os.path.join(SUBDIR, f"agent-{AGENT}.meta.json")
    STATE_KEY = "state:agent." + AGENT          # audit label for checkpoint rows
    USAGE_KEY = "usage_last:" + AGENT           # kv slot for the usage dedup record
    BILLED_KEY = "billed:" + AGENT              # kv slot: {in,out,cache,create,create_1h} this
                                                # streamer chain has folded into the
                                                # scoreboard — the baseline reconcile_spend
                                                # (claude-subagent-fmt.py) diffs the
                                                # transcript's true total against, so a
                                                # crashed streamer's un-bumped tail is
                                                # recovered exactly once at SubagentStop.

    # The block renderer: transcript records -> mirror paint ops (substream_render.py).
    # Everything identity-shaped is injected here; the Renderer holds the per-run render
    # state (pending message, pend ledger, ctx-tag turn tracking, the footer's rollup).
    REN = SR.Renderer(log=LOG, agent=AGENT, label=LABEL, rgb=SUB_RGB, sub_fg=SUB_FG,
                      op_tag=op_tag, ctx_tag=ctx_tag, take_subfg=take_subfg,
                      spawn_fg_tailer=spawn_fg_tailer, spawn_tailer=spawn_tailer)


# Context-fill thresholds (percent) for the live per-turn % shown on each turn:
# < WARN green, < CRIT amber, else red. Tunable per workload via the env, same as
# CLAUDE_MIRROR_BIAS — e.g. a [1m] session wants higher cutoffs.
CTX_WARN = M.int_env("CLAUDE_MIRROR_CTX_WARN", 30)
CTX_CRIT = M.int_env("CLAUDE_MIRROR_CTX_CRIT", 60)

RESOLVED_MODEL = None      # authoritative model id (with [1m]) read from the parent

short_model = M.short_model


def disp_model():
    # The model to display, best-known-first: the agent's own resolved id > this agent's
    # configured model (meta) or an explicit frontmatter override > the parent session's
    # version (for inheriting agents, before the first turn) > footer id > config alias.
    return (REN.last_model or META.get("model") or AGENT_DEF_MODEL or SESSION_MODEL
            or RESOLVED_MODEL or SETTINGS_MODEL)


def effort():
    # Configured effort (env > frontmatter > session) if any, else the running model's
    # default — so an agent that inherits shows the level it actually reasons at.
    return EFFORT_CFG or M.model_default_effort(disp_model())


def op_tag():
    # "opus-4.8·high" — the model this agent is running plus the resolved effort.
    # Constant per agent; appended to every operation header.
    return "·".join(x for x in (short_model(disp_model()), effort()) if x)

def cancelled_by_user():
    # A manually killed/cancelled subagent fires NO SubagentStop hook — the same
    # gap documented throughout this codebase for interrupts (claude-tab-status.py's
    # idle-watch, claude-cmd-pre.py's cancelled-foreground-command fix) — so the
    # done flag never flips and this tailer would otherwise hang until the 6h backstop
    # below, leaving the tab stuck blue the whole time. But Claude Code stamps
    # `stoppedByUser: true` onto this agent's meta.json sidecar the moment that
    # happens (confirmed empirically), giving a fast, reliable end signal instead.
    try:
        with open(META_PATH, encoding="utf-8") as fh:
            return bool(json.load(fh).get("stoppedByUser"))
    except Exception:
        return False


kfmt = O.kfmt        # compact token count: 124000 -> "124k"


def model_ctx():
    # Context window for the fill %, derived purely from config/model — NO empirical
    # self-correct. Precedence, first that resolves wins:
    #   0. CLAUDE_CODE_DISABLE_1M_CONTEXT — Claude Code's own kill-switch, caps at 200k
    #   1. RESOLVED_MODEL — authoritative id from the parent transcript (footer only)
    #   2. AGENT_DEF_MODEL — an explicit `model:` in this agent's definition frontmatter
    #   3. REN.last_model — the bare id the agent actually ran (family is reliable; the
    #      known-1M table covers Opus 4.8 etc. even though the [1m] suffix is stripped)
    #   4. SETTINGS_MODEL — the session default, for agents that inherit
    return M.context_window(RESOLVED_MODEL, AGENT_DEF_MODEL, REN.last_model, SETTINGS_MODEL)


def ctx_used():
    # The occupied context window for the latest assistant turn: every input token the
    # model saw — fresh + just-cached + replayed-from-cache. output_tokens is excluded
    # (that's what it produced back, not context). 0 if no usage seen yet.
    if not REN.last_usage:
        return 0
    return (REN.last_usage.get("input_tokens", 0)
            + REN.last_usage.get("cache_creation_input_tokens", 0)
            + REN.last_usage.get("cache_read_input_tokens", 0))


def ctx_tag():
    # Plain "ctx 42% · 84k/200k" for the current turn, or "" if no usage. Rendered as
    # dark text inside the operation chip (see Renderer.chip()), so no inline threshold
    # colour — the chip's own solid background carries the identity hue.
    used = ctx_used()
    if used <= 0:
        return ""
    mx = model_ctx()
    return f"ctx {used * 100 // mx}% · {kfmt(used)}/{kfmt(mx)}"


alive = S.pid_alive                 # EPERM (foreign-owned) counts as alive


def spawn_tailer(kind, taskid, cmd="", group=None):
    # Stream a subagent's background/monitor job with a DOUBLE gutter (outer = this
    # subagent's colour, inner = the job's own palette slot). claude-stream.py argv:
    #   KIND TASKID LOG SLOT SIG OUTER
    # group (a tool_use_id) rides in via CLAUDE_STREAM_GROUP so the tailer's ops join
    # the block's ⧉ copy group (see core/copy.py / claude-stream.py's GROUP).
    streamer = os.path.join(BIN, "claude-stream.py")
    if not (taskid and os.path.exists(streamer)):
        return
    slot, marker = claude_slots.claim(kind, LOG)
    # monitor_sig is the ONE owner of the signature extraction (the wire
    # contract with claude-stream.py's find_proc).
    sig = ST.monitor_sig(cmd) if kind == "monitor" else ""
    outer = ",".join(str(x) for x in SUB_RGB)
    env = HK.stream_env(cmd=cmd, group=group)
    try:
        proc = subprocess.Popen(
            [sys.executable, streamer, kind, taskid, LOG, str(slot), sig, outer],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True, env=env)
        claude_slots.set_owner(marker, proc.pid)
        A.spawn(LOG, proc.pid, [streamer, kind, taskid, str(slot)],
                purpose=f"stream:{kind} (nested under agent {AGENT[:8]})")
    except Exception:
        A.error(LOG, "spawn_tailer", {"kind": kind, "taskid": taskid, "agent": AGENT})
        claude_slots.release(kind, LOG, slot, os.getpid())


def take_subfg(tid):
    # Consume the tee hand-off claude-cmd-pre.py's PreToolUse left for this fg command
    # (keyed by tool_use_id). That hook fires as the command dispatches — the transcript
    # tool_use line we're reading can arrive a beat before it, so wait briefly rather
    # than miss the live tail. Only reached when SUB_FG is on, so a disabled feature
    # (no marker ever written) never pays this wait.
    deadline = time.time() + 1.5
    while True:
        rec = S.hand_take(LOG, "subfg:" + tid)
        if rec is not None:
            A.state_file(LOG, "state:subfg:" + tid, "remove", "consumed by substream")
            return rec
        if time.time() >= deadline:
            return None
        time.sleep(0.05)


def spawn_fg_tailer(tid, rec, cmd=""):
    # Live-tail a subagent's FOREGROUND command (tee'd by claude-cmd-pre.py) with the
    # main fg tailer (claude-stream.py KIND=fg), double-guttered in THIS subagent's
    # colour — the foreground analogue of spawn_tailer's nested bg/monitor jobs. The
    # fg tailer waits for the outcome hand-off we drop in on_tool_result (keyed by
    # rec["done"]), or falls back to writer-liveness. `cmd` is the transcript's
    # tool_use command (the model-authored original — updatedInput rewrites the
    # EXECUTED input, not the assistant message), so the tailer's content-render
    # detection sees the same clean command the main-session path passes.
    streamer = os.path.join(BIN, "claude-stream.py")
    if not os.path.exists(streamer):
        return None
    slot, marker = claude_slots.claim("fg", LOG)
    outer = ",".join(str(x) for x in SUB_RGB)
    env = HK.stream_env(src=rec["src"], done=rec["done"], cmd=cmd, group=tid,
                        own=bool(rec.get("own")),
                        skip_existing=bool(rec.get("append")))
    try:
        proc = subprocess.Popen(
            [sys.executable, streamer, "fg", "subfg-" + tid, LOG, str(slot), "", outer],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True, env=env)
        claude_slots.set_owner(marker, proc.pid)
        A.spawn(LOG, proc.pid, [streamer, "fg", "subfg-" + tid, str(slot)],
                purpose=f"stream:fg live (subagent {AGENT[:8]} foreground cmd)")
        return proc
    except Exception:
        A.error(LOG, "spawn_fg_tailer", {"tid": tid, "agent": AGENT})
        claude_slots.release("fg", LOG, slot, os.getpid())
        return None


def restore_checkpoint():
    # Resume from the previous streamer's checkpoint (idle-teammate restart) so
    # already-rendered history isn't replayed. The persisted usage record is the
    # predecessor's last-counted usage, restored so a message straddling the handoff
    # isn't recounted from zero. Ignore a checkpoint past EOF (a rewritten/foreign
    # transcript) and start over. The adopted-vs-fresh outcome is audited (one row
    # per streamer, not per pump — the per-tick writes are too hot): paired with the
    # predecessor's 'final' row it makes a bad handoff (recounted or skipped
    # transcript, dropped dedup state) visible in `state_files`.
    pos = 0
    resume = {"agent": AGENT}
    try:
        saved = int(S.agent_get(LOG, AGENT).get("pos") or 0)
        if 0 < saved <= os.path.getsize(JSONL):
            pos = saved
            lu = S.kv_get(LOG, USAGE_KEY)
            if isinstance(lu, dict) and lu.get("id"):
                if "f" not in lu:   # predecessor predates O.usage_fold's record shape
                    lu = {"id": lu.get("id"),
                          "f": [int(lu.get(k) or 0) for k in ("in", "out", "cache", "create")]}
                REN.usage_last = lu
            resume.update({"adopted_pos": pos, "usage_last": REN.usage_last})
        elif saved:
            resume["fresh"] = f"checkpoint {saved} empty or past EOF"
        else:
            resume["fresh"] = "no checkpoint (first streamer)"
    except Exception:
        resume["fresh"] = "unreadable checkpoint"
    A.state_file(LOG, STATE_KEY, "resume", resume)
    return pos


def make_pump(tail, ckpt):
    def pump():
        lines = tail.pump()
        for ln in (lines or ()):
            s = ln.decode("utf-8", "replace").strip()
            if s:
                REN.handle_line(s)
        # Checkpoint only what was fully consumed — a trailing partial line
        # stays uncounted so a successor re-reads it whole. The last-counted
        # usage record rides along for the successor's dedup.
        if tail.consumed != ckpt["pos"]:
            ckpt["pos"] = tail.consumed
            S.agent_set(LOG, AGENT, pos=tail.consumed)
            if REN.usage_last:
                S.kv_set(LOG, USAGE_KEY, REN.usage_last)
    return pump


def make_parent_resolved(start):
    # A REJECTED (or otherwise abandoned) Task fires no SubagentStop AND leaves
    # meta.json without stoppedByUser, so neither of the other signals ever comes —
    # the streamer (and its sub.pid slot row, the tab's liveness signal → a stuck-blue
    # tab) would then hang until the 6h backstop. But the PARENT transcript records
    # the Task's tool_result the instant the call resolves, keyed by meta.json's
    # toolUseId. Tail it (from its current end — the result lands later) as a
    # fallback end signal. This is an EVENT, not an idle timeout: it does NOT
    # reintroduce the banned idle-watch backstop (which false-positived on long
    # thinks). Checked below the done/stoppedByUser signals and lightly throttled,
    # so a normal finish still exits on its authoritative stop-sentinel.
    parent_tid = (META.get("toolUseId") or "").strip()
    parent_tail = None
    if parent_tid and os.path.exists(TPATH):
        try:
            parent_tail = T.FileTailer(TPATH, pos=os.path.getsize(TPATH))
        except Exception:
            parent_tail = None
    # Scan throttle (env knob is test-only — see docs/testing.md; unset, 2 s as always).
    scan_s = float(os.environ.get("CLAUDE_STREAM_PARENT_SCAN_S") or 2.0)
    state = {"next": start + scan_s}         # next time the parent scan is allowed

    def parent_resolved():
        # None = not resolved (or throttled); bool = resolved, value is is_error
        # (True == user rejected/cancelled the Task).
        if parent_tail is None or time.time() < state["next"]:
            return None
        state["next"] = time.time() + scan_s
        res = None
        try:
            for ln in (parent_tail.pump() or ()):
                r = M.parent_tool_result(ln.decode("utf-8", "replace"), parent_tid)
                if r is not None:
                    res = r                  # last hit wins (there is only one)
        except Exception:
            return None
        return res
    return parent_resolved


def completion_loop(run, pump, parent_resolved, start):
    # Completion: the SubagentStop sentinel (the authoritative end signal — written
    # by the stop hook) for a normal finish, OR meta.json's stoppedByUser for a
    # manual cancel (see cancelled_by_user() above — no hook fires for that case),
    # OR the parent Task result resolving (parent_resolved() — the rejected/abandoned
    # case neither of those covers), OR the state DB vanishing (SessionEnd parked it
    # as *.keep — quitting Claude Code kills a background agent with no SubagentStop
    # and no stoppedByUser stamp, so without this check the streamer spun for the
    # full backstop as a zombie while its checkpoint writes mutated the parked
    # snapshot through the cached connection; the codex tailers run the same check).
    # A long cap is a backstop for a stuck/lost streamer either way.
    # Returns (parked, cancelled).
    while True:
        pump()
        if S.parked(LOG):
            run.end("state-db-parked (session end)")
            return True, False
        if S.agent_get(LOG, AGENT).get("done"):
            run.end("stop-sentinel")
            return False, False
        if cancelled_by_user():
            run.end("stoppedByUser (manual cancel)")
            return False, True
        pr = parent_resolved()
        if pr is not None:
            # rejected -> "cancelled" footer
            run.end("parent-task-resolved" + (" (rejected)" if pr else ""))
            return False, bool(pr)
        if time.time() - start > T.BACKSTOP_S:
            run.end("backstop-timeout")
            return False, False
        time.sleep(T.POLL_S)


def emit_footer(cancelled, start, tail):
    got = claude_slots.lookup_id("sub", LOG, AGENT)
    ts = got[1] if (got and got[1]) else start
    sec = max(0.0, time.time() - ts)
    dur = O.fmt_dur(sec)
    foot = f"■ {LABEL} " + ("cancelled" if cancelled else "ended") + f" · {dur}"
    global RESOLVED_MODEL
    RESOLVED_MODEL = M.parent_resolved_model(TPATH, AGENT)   # authoritative window, best-effort
    used = ctx_used()                    # final context fill (plain — the chip is dark text)
    if used > 0:
        mx = model_ctx()
        foot += f" · ctx {used * 100 // mx}% ({kfmt(used)}/{kfmt(mx)})"
    # Cumulative rollup: fresh in / generated out / cache-hit share / tool count.
    foot += SF.tok_rollup(REN.tot_in, REN.tot_out, REN.tot_cache)
    if REN.tool_n:
        foot += f" · {REN.tool_n} tool" + ("s" if REN.tool_n != 1 else "")
    # Cost estimate from the tokens already summed, priced on the resolved model
    # (cache_creation billed at its write premium via tot_create — 1.25×, plus the
    # tot_create_1h share's extra to reach the 1h TTL's 2×).
    usd = ACC.cost_usd(disp_model(), REN.tot_in, REN.tot_out, REN.tot_cache,
                       REN.tot_create, REN.tot_create_1h)
    if usd:
        foot += " · ≈ " + O.fmt_usd(usd)
    O.emit(LOG, O.rule(), O.label(foot, SUB_RGB), O.rule())
    # Checkpoint-trail bookend to the 'resume' row above: what this streamer
    # consumed and last counted. A successor whose 'resume' row disagrees with this
    # 'final' row is the handoff bug the persisted usage_last exists to prevent.
    A.state_file(LOG, STATE_KEY, "final",
                 {"agent": AGENT, "pos": tail.consumed, "usage_last": REN.usage_last,
                  "in": REN.tot_in, "out": REN.tot_out, "cache": REN.tot_cache,
                  "create": REN.tot_create, "create_1h": REN.tot_create_1h})
    # NOTE: this agent's token/cost spend is NO LONGER bumped into the scoreboard
    # here. Cost is now OTEL-authoritative — the OTLP receiver (plugins/otel/) folds
    # every agent request (query_source=subagent) into the session counters live, so
    # a footer bump would double-count. The `≈ $usd` above is display-only (priced by
    # ACC.cost_usd for the footer text). The BILLED_KEY baseline below is retained: it
    # feeds reconcile_spend's OTEL-vs-transcript cross-check audit row at SubagentStop.
    # Advance the cumulative-billed baseline (across the whole streamer chain — each
    # generation is its own process, so tot_* is just this generation's delta). A
    # crash BEFORE this point leaves the baseline behind the transcript's true total,
    # and reconcile_spend bumps exactly that gap at SubagentStop. Single writer (no
    # concurrent streamer for one agent_id), so a plain read-add-write is safe.
    _pv = S.kv_get(LOG, BILLED_KEY) or {}
    S.kv_set(LOG, BILLED_KEY, {"in": int(_pv.get("in") or 0) + REN.tot_in,
                               "out": int(_pv.get("out") or 0) + REN.tot_out,
                               "cache": int(_pv.get("cache") or 0) + REN.tot_cache,
                               "create": int(_pv.get("create") or 0) + REN.tot_create,
                               "create_1h": int(_pv.get("create_1h") or 0) + REN.tot_create_1h})


def main(run):
    start = time.time()
    # Wait for the transcript to appear.
    if not T.wait_for(JSONL, start + 15):
        O.emit(LOG, O.rule(), O.label(f"■ {LABEL} (no transcript)", SUB_RGB), O.rule())
        run.end("transcript-never-appeared")
        return

    pos = restore_checkpoint()
    tail = T.FileTailer(JSONL, pos=pos)
    pump = make_pump(tail, {"pos": -1})
    parent_resolved = make_parent_resolved(start)

    parked, cancelled = completion_loop(run, pump, parent_resolved, start)
    if parked:
        # No footer, no bumps, no checkpoint past this point: every write
        # would either recreate the state DB (whose file-existence IS the
        # session-alive signal watchers poll) or land in the parked snapshot.
        return

    # Final drain — let the last lines land, then read them.
    time.sleep(0.3)
    pump(); pump()
    REN.flush_msg(is_result=True)    # the last buffered message is the returned result

    emit_footer(cancelled, start, tail)


def cleanup():
    # Release this agent's markers FIRST (so the recheck below doesn't see our own
    # still-live sub.pid), then ask claude-tab-status.py to flip a stale bg-running
    # blue back to green — a background agent finishing has no other hook to do it.
    # (No-op unless the tab is currently awaiting-bg and nothing else is running.)
    claude_slots.release_id("sub", LOG, AGENT)
    # Clear the done flag (NOT pos — a resumed teammate needs the checkpoint) so a
    # later SubagentStart for this agent_id doesn't finalise its new streamer at once.
    S.agent_set(LOG, AGENT, done=0)
    claude_slots.pid_del(LOG, AGENT)
    try:
        subprocess.run([os.path.join(BIN, "claude-tab-status.py"), "bg-recheck", LOG, "sub"],
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=10)
    except Exception:
        pass


def entry():
    _init(sys.argv)          # argv/env/meta binding happens only at run time
    with T.stream_lifecycle(LOG, "teammate" if PALETTE == "team" else "subagent",
                            agent_id=AGENT, src_path=JSONL,
                            ctx={"agent": AGENT, "type": ATYPE}) as run:
        def _finalize():
            run.lines = REN.tool_n      # tools seen — recorded even on a crash
            cleanup()
        run.on_exit = _finalize
        main(run)
