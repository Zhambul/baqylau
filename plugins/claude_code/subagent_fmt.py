# plugins/claude_code/subagent_fmt.py — subagent block driver (push/start/stop)
# Entry point: claude-subagent-fmt.py (a thin shim — the entry FILENAME is the audit vocabulary).
# claude-subagent-fmt.py MIRROR_LOG PHASE
#
# Drives a subagent's block in the kitty command mirror. PHASE is:
#   push  — PreToolUse(Task/Agent): stash the task description for the upcoming
#           SubagentStart header (SubagentStart's payload doesn't carry it, and the
#           on-disk meta.json with it isn't written until the subagent finishes).
#   start — SubagentStart: pop the description, claim the agent's colour slot, write
#           the "▶ <type> · <desc>" header, and spawn claude-substream.py to tail
#           the subagent's transcript (its prompt, messages, commands, results).
#   stop  — SubagentStop: signal completion to the streamer (which writes the footer
#           and releases the slot). Safety net: if the streamer isn't running, write
#           the footer + release the slot here.
import os, sys, time

from core import ops as O
from core import slots as claude_slots
from core import state as S
from plugins.claude_code import accounting as ACC
from plugins.claude_code import hookkit as H
from plugins.claude_code import model as M

A = O.A    # audit trail (real module, or a no-op stub if it failed to import)

PHASE = sys.argv[1] if len(sys.argv) > 1 else "start"
LOG   = ""   # set in main() from the payload's session_id (per-session log)


def fmt_dur(sec):
    return O.fmt_dur(sec) if sec > 0 else ""


def is_teammate(tpath, agent_id):
    # An in-process agent-team teammate is, at the storage layer, just a subagent —
    # but its meta.json carries taskKind == "in_process_teammate". (Unlike an ordinary
    # subagent's meta, a teammate's is present at SubagentStart, so reading it here is
    # reliable.) Teammates render in the lighter "team" palette + a "teammate" header.
    # The meta.json read (incl. the brief retry while it lags SubagentStart — without
    # which the race made real teammates render in the ordinary subagent palette) is
    # M.agent_meta's; missing/never-appearing meta → {} → False.
    return M.agent_meta(tpath, agent_id).get("taskKind") == "in_process_teammate"


# The canonical probe treats EPERM (exists, foreign-owned) as ALIVE — the local
# copy this replaces returned False there, which could misread a live streamer
# as dead and fire the safety-net footer spuriously.
alive = S.pid_alive


def reconcile_spend(log, tpath, agent_id, team, ajsonl=""):
    """Cross-check an agent's transcript-derived spend against the OTEL-authoritative
    scoreboard at SubagentStop. Cost is now folded live by the OTLP receiver
    (plugins/otel/, query_source=subagent) — including the tail a crashed streamer
    would have dropped — so this NO LONGER bumps counters. It still folds the agent's
    FULL transcript to its TRUE total (claude_ops.fold_usage, deduped by message.id),
    records the residual over the BILLED_KEY baseline as a `reconcile` audit row (an
    OTEL-vs-transcript reconciliation trail), and advances the baseline/render cursor
    so a later resume renders past what we folded. `ajsonl` is the payload's
    agent_transcript_path when it carried one (authoritative; the derived subagents/
    path is the fallback). Returns a short status for the stop decision row —
    'reconciled', 'already billed', or 'no transcript' (the hidden-summarizer-agent
    case: a stop for an agent whose transcript was never written — OTEL still captured
    its spend, so this is no longer a gap, just a note). Best-effort; never raises."""
    try:
        base = tpath[:-6] if tpath.endswith(".jsonl") else tpath
        jsonl = ajsonl or os.path.join(base, "subagents", f"agent-{agent_id}.jsonl")
        if not (tpath or ajsonl) or not os.path.exists(jsonl):
            return "no transcript"
        ti, to, tc, tcr, t1h, carry, consumed = ACC.fold_usage(jsonl, 0, None)
        key = "billed:" + agent_id
        prev = S.kv_get(log, key) or {}
        r_in = ti - int(prev.get("in") or 0)
        r_out = to - int(prev.get("out") or 0)
        r_cache = tc - int(prev.get("cache") or 0)
        r_create = tcr - int(prev.get("create") or 0)
        r_1h = t1h - int(prev.get("create_1h") or 0)
        if r_in <= 0 and r_out <= 0 and r_cache <= 0 and r_create <= 0:
            return "already billed"             # streamer already billed everything
        model = (M.parent_resolved_model(tpath, agent_id)
                 or M.agent_meta(tpath, agent_id).get("model"))
        usd = ACC.cost_usd(model, r_in, r_out, r_cache, r_create, max(r_1h, 0))
        deltas = {}
        if usd:
            deltas["cost"] = usd
        if r_in or r_out:
            deltas["tokens"] = r_in + r_out     # fresh billed input(+create) + output
        if r_in or r_out or r_cache or r_create:
            # Same Σ-row split the streamer footer uses — O.split_tokens owns the
            # fields→tk_* arithmetic (r_in is input+create, so tk_in subtracts).
            deltas.update(O.split_tokens(r_in, r_out, r_cache, r_create))
        if not deltas:
            return "already billed"
        # Cost is OTEL-authoritative now (the OTLP receiver folds agent spend live),
        # so we NO LONGER bump these residual deltas into the scoreboard — that would
        # double-count. We still fold the transcript and record the residual below as
        # an OTEL-vs-transcript cross-check (the `reconcile` audit row), and advance
        # the baselines/cursor so a later resume renders past what we folded.
        S.kv_set(log, key, {"in": ti, "out": to, "cache": tc, "create": tcr,
                            "create_1h": t1h})
        # Advance the resume checkpoint to the folded EOF so a LATER SubagentStart
        # for this agent (idle-teammate wake after a crash) resumes past what we just
        # billed — otherwise its new streamer re-folds [pos, EOF] and double-counts.
        # `pos` is the render/resume cursor; USAGE_KEY is the straddle-dedup carry.
        S.agent_set(log, agent_id, pos=consumed)
        if carry:
            S.kv_set(log, "usage_last:" + agent_id, carry)
        A.state_file(log, "state:agent." + agent_id, "reconcile",
                     {"agent": agent_id,
                      "residual": {"in": r_in, "out": r_out, "cache": r_cache,
                                   "create": r_create, "create_1h": r_1h},
                      "cost": usd,
                      "true": {"in": ti, "out": to, "cache": tc, "create": tcr,
                               "create_1h": t1h}})
        return "reconciled"
    except Exception:
        A.error(log, "reconcile_spend", {"agent": agent_id})
        return "error"


def finalize(log, d, agent_id, atype, tpath, tag="stop"):
    """Signal the agent's streamer to finalise its block and release its colour slot,
    writing a safety-net footer if the streamer already died. Reached from two stop
    signals:
      • SubagentStop (tag="stop") — the normal end of a subagent/teammate.
      • StopFailure carrying an agent_id (tag="stopfail", from claude-stop-fmt.py) —
        a subagent turn that DIED on an API error (e.g. 529 Overloaded). Claude Code
        fires NO SubagentStop (nor stamps meta.json stoppedByUser) for it, so this
        StopFailure is the agent's ONLY stop signal; without acting on it the streamer
        keeps its sub.pid slot row — the tab's liveness signal — claimed forever,
        wedging the tab blue. An ASYNC background agent makes it worse still: its parent
        tool_result is only the "launched successfully" ack (no is_error), so the
        streamer's parent-task-resolved recovery never fires either.

    A background agent's stop can fire MORE THAN ONCE. After the first stop the streamer
    finalises and releases its slot, so a later duplicate finds no slot and does NOTHING
    (else it painted a spurious indigo "■ agent ended", slot 0, no duration). The stop
    signal is the agent record's `done` flag (was a sub.done.* sentinel); the streamer
    polls it and finalises, then cleanup clears it so a later RESUME of the same agent_id
    doesn't finalise its new streamer instantly. `started` is read BEFORE the done write
    creates the record: an agent with no slot on record never saw a SubagentStart here
    (Claude Code runs hidden summarizer agents that fire ONLY SubagentStop — no start,
    usually no transcript, so their spend is real but unfoldable)."""
    started = S.agent_get(log, agent_id).get("slot") is not None
    S.agent_set(log, agent_id, done=1)
    A.state_file(log, "state:agent." + agent_id, "write", "done=1 (stop signal for streamer)")
    _p = claude_slots.pid_get(log, agent_id)
    running = bool(_p) and alive(_p)
    if not running:
        # The streamer is gone. If it died before its footer (crash/kill), its
        # un-bumped token tail is still on disk — fold it into the scoreboard now.
        # Idempotent (diffs against the billed baseline), so a duplicate stop or a
        # clean finish recovers nothing. The payload's agent_transcript_path (when
        # present) beats the derived path.
        team = is_teammate(tpath, agent_id)   # once — each probe is a retry-read
        rec_st = reconcile_spend(log, tpath, agent_id, team,
                                 d.get("agent_transcript_path") or "")
        got = claude_slots.lookup_id("sub", log, agent_id)
        # Release FIRST, emit only if THIS call deleted the row: two overlapping
        # duplicate stops could both pass the lookup and both paint the footer — the
        # atomic DELETE's rowcount (release_id's return) is the once-only licence.
        if got and claude_slots.release_id("sub", log, agent_id):
            dur = fmt_dur(time.time() - got[1]) if got[1] else ""
            chip = f"■ {atype} ended · {dur}" if dur else f"■ {atype} ended"
            pal = "team" if team else "sub"
            O.emit(log, O.rule(), O.label(chip, claude_slots.color(pal, got[0])), O.rule())
            A.hook_event(d, decision="%s: SAFETY NET footer (streamer died mid-run, "
                         "spend %s)" % (tag, rec_st))
        elif not started:
            # Not a duplicate — this agent NEVER started here. "no transcript" is the
            # scoreboard-under-/cost tell (billed spend the fold structurally can't see).
            A.hook_event(d, decision="%s: never started (hidden agent) — spend %s"
                         % (tag, rec_st))
        else:
            A.hook_event(d, decision="%s: no-op (already finalised / duplicate stop; "
                         "spend %s)" % (tag, rec_st))
        S.agent_set(log, agent_id, done=0)        # don't wedge a future resume
        claude_slots.pid_del(log, agent_id)
    else:
        A.hook_event(d, decision="%s: done flag set, streamer will finalise" % tag)


def main():
    global LOG
    d, LOG = H.read_payload()
    if d is None:
        return

    # PreToolUse(Agent): stash the task description for the upcoming SubagentStart.
    if PHASE == "push":
        ti = d.get("tool_input") or {}
        claude_slots.desc_push(LOG, ti.get("description") or "")
        A.hook_event(d, decision="pushed description: " + (ti.get("description") or ""))
        return

    agent_id = d.get("agent_id")
    if not agent_id:
        return H.ignore(d, "no agent_id")
    atype = d.get("agent_type") or "agent"
    tpath = d.get("transcript_path") or ""

    if PHASE == "start":
        # Teammates reuse the subagent slot machinery (claim_id "sub" + sub.* markers,
        # so bg_command_running keeps the tab blue while one runs); only the colour
        # palette + header keyword differ.
        team = is_teammate(tpath, agent_id)
        pal  = "team" if team else "sub"
        # A resumed teammate (see the `resumed` block below) must keep its ORIGINAL
        # colour — one hue per agent identity — so its first slot is persisted on the
        # agent's state-DB record (was a sub.slot.* file) and pinned on re-claim
        # instead of taking the next round-robin.
        rec = S.agent_get(LOG, agent_id)
        prefer = rec.get("slot") if rec.get("slot") is not None else None
        slot, is_new = claude_slots.claim_id("sub", LOG, agent_id, prefer=prefer)
        if is_new and prefer is None:
            S.agent_set(LOG, agent_id, slot=slot)
        # A background agent (and a teammate in particular) can fire SubagentStart
        # MORE THAN ONCE. If we already claimed this agent's slot and its streamer is
        # still live, this is a duplicate start — don't write a second header or spawn
        # a second streamer (which would re-render the whole transcript a second time).
        if not is_new:
            _p = claude_slots.pid_get(LOG, agent_id)
            if _p and alive(_p):
                A.hook_event(d, decision="ignored: duplicate SubagentStart, streamer live")
                return
        rgb = claude_slots.color(pal, slot)
        # An idle teammate that wakes on a new message fires SubagentStart AGAIN with
        # the same agent_id after its previous streamer fully finalised. That resume is
        # recognisable by the streamer's surviving position checkpoint (the agent
        # record's `pos` — was a sub.pos.* file). On resume there was no
        # PreToolUse(Agent) push, so desc_pop() would steal a description queued for a
        # DIFFERENT agent — reuse the one persisted at first start instead, and mark
        # the header ↻ so the block reads as a continuation.
        resumed = rec.get("pos") is not None
        if resumed:
            desc = (rec.get("desc") or "").strip()
        else:
            desc = claude_slots.desc_pop(LOG)
            if desc:
                S.agent_set(LOG, agent_id, desc=desc)
        glyph = "↻" if resumed else "▶"
        if team:
            head = f"{glyph} {atype} · teammate · {desc}" if desc else f"{glyph} {atype} · teammate"
        else:
            head = f"{glyph} {atype} · {desc}" if desc else f"{glyph} {atype}"
        O.emit(LOG, O.blank(), O.rule(), O.label(head, rgb), O.rule())
        # Spawn the transcript streamer (detached) and record its pid so `stop` can
        # tell whether it's still running. PALETTE (argv 6) tells it which colour
        # family to use — must match the header colour chosen just above.
        spawned = None
        if tpath:
            proc = H.spawn_streamer(
                "claude-substream.py",
                [agent_id, tpath, LOG, slot, atype, pal, desc], LOG,
                purpose=f"stream:{'teammate' if team else 'subagent'} {atype}",
                audit_argv=[agent_id, str(slot), atype, pal])
            if proc is not None:
                claude_slots.pid_set(LOG, agent_id, proc.pid)
                spawned = proc.pid
        A.hook_event(d, decision="start: %s slot=%s%s%s streamer=%s"
                     % ("teammate" if team else "subagent", slot,
                        " resumed" if resumed else "", "" if is_new else " re-claim",
                        spawned or "FAILED"))
        # A subagent just started -> the main session is awaiting it, so turn the tab
        # BLUE (even if the lead's turn had ended green). SubagentStart otherwise never
        # touches the tab, so a TEAMMATE working between the lead's turns would leave it
        # stuck green. Fire for ANY subagent, not just detected teammates: is_teammate()
        # is racy (the meta.json can lag SubagentStart), so gating on it dropped real
        # teammates into green. A foreground subagent is already blue via the lead's
        # blocked turn, so this is at worst a no-op for them.
        H.notify_tab("agent-start", [LOG], LOG)
        return

    # stop: hand off to the shared finaliser — it signals the streamer (the SOLE
    # writer of the footer + slot release) via the agent record's `done` flag and
    # writes a safety-net footer only if the streamer already died mid-run. The same
    # finaliser is reached from claude-stop-fmt.py when a StopFailure carries an
    # agent_id (an API-error stop that fires no SubagentStop); see finalize().
    finalize(LOG, d, agent_id, atype, tpath)


def run_phase(phase):
    """In-process entry for the single per-event dispatcher (dispatch.py): set the
    PHASE this call represents (push/start/stop) and run main() against the
    dispatcher-injected payload (H.set_payload). The standalone shim path keeps
    reading PHASE from argv[1] via the module global above."""
    global PHASE
    PHASE = phase
    main()


def entry():
    H.run(main, phase=PHASE)
