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
import json, os, sys, time

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
    base = tpath[:-6] if tpath.endswith(".jsonl") else tpath
    meta = os.path.join(base, "subagents", f"agent-{agent_id}.meta.json")
    # The meta.json can lag SubagentStart by a moment, so retry briefly when it's
    # missing (no delay when it's already there). Without this, the race made real
    # teammates render in the ordinary subagent palette.
    for _ in range(6):
        try:
            with open(meta, encoding="utf-8") as f:
                return json.load(f).get("taskKind") == "in_process_teammate"
        except Exception:
            time.sleep(0.08)
    return False


# The canonical probe treats EPERM (exists, foreign-owned) as ALIVE — the local
# copy this replaces returned False there, which could misread a live streamer
# as dead and fire the safety-net footer spuriously.
alive = S.pid_alive


def reconcile_spend(log, tpath, agent_id, team, ajsonl=""):
    """Recover any agent token spend a dead/crashed streamer never folded into the
    scoreboard. The streamer bumps an agent's spend only at its footer; a crash
    (the `.strip()`-on-dict bug was one cause) — or any exit before the footer —
    drops the un-bumped tail. Run at SubagentStop once the streamer is gone: fold
    the agent's FULL transcript to its TRUE total (claude_ops.fold_usage, deduped by
    message.id) and bump only the residual over what the streamer chain already
    billed (the BILLED_KEY baseline the footer advances). Idempotent — a clean
    finish leaves true == baseline, so this bumps nothing, and a duplicate stop
    re-folds to the same total. `ajsonl` is the payload's agent_transcript_path
    when it carried one (authoritative; the derived subagents/ path is the
    fallback). Returns a short status for the stop decision row — 'reconciled',
    'already billed', or 'no transcript' (the hidden-summarizer-agent case: a
    stop for an agent whose transcript was never written; its spend is real but
    unfoldable). Best-effort; never raises into the hook."""
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
            # Same Σ-row split the streamer footer uses: tk_in is fresh input EXCL.
            # cache creation (r_in is input+create), so tk_in+tk_create == r_in.
            deltas["tk_in"] = r_in - r_create
            deltas["tk_out"] = r_out
            deltas["tk_read"] = r_cache
            deltas["tk_create"] = r_create
        if not deltas:
            return "already billed"
        O.bump(log, meta={"agent_id": agent_id,
                          "kind": "teammate" if team else "subagent",
                          "model": model, "in": r_in, "out": r_out,
                          "cache": r_cache, "create": r_create, "create_1h": r_1h,
                          "src": jsonl, "reconcile": True}, **deltas)
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

    # stop: signal completion to the streamer, which is the SOLE writer of the
    # footer (and releases the slot). We only write a footer here as a safety net,
    # and ONLY when the streamer truly isn't running AND it still holds a claimed
    # slot — i.e. it died mid-run without closing the block.
    #
    # A background agent's SubagentStop can fire MORE THAN ONCE ("may notify more
    # than once"). After the first stop the streamer finalises and releases its
    # slot, so a later duplicate stop finds no slot (lookup_id -> None) and we do
    # NOTHING — otherwise it printed a spurious indigo "■ agent ended" (slot 0,
    # no duration). Requiring a still-claimed slot is what suppresses that.
    # The stop signal is the agent record's `done` flag (was a sub.done.* sentinel
    # file) — the streamer polls it and finalises; cleanup clears it back to 0 so a
    # later RESUME of the same agent_id doesn't finalise its new streamer instantly.
    # `started` is read BEFORE the done write creates the record: an agent with no
    # slot on record never saw a SubagentStart here (Claude Code runs hidden
    # summarizer-style agents that fire ONLY SubagentStop — no start, and usually no
    # transcript on disk either, so their spend is real but unfoldable; see the
    # SubagentStop-without-SubagentStart anomaly).
    started = S.agent_get(LOG, agent_id).get("slot") is not None
    S.agent_set(LOG, agent_id, done=1)
    A.state_file(LOG, "state:agent." + agent_id, "write", "done=1 (stop signal for streamer)")
    _p = claude_slots.pid_get(LOG, agent_id)
    running = bool(_p) and alive(_p)
    if not running:
        # The streamer is gone. If it died before its footer (crash/kill), its
        # un-bumped token tail is still on disk — fold it into the scoreboard now.
        # Idempotent (diffs against the billed baseline), so a duplicate stop or a
        # clean finish recovers nothing. Runs even when the crashed streamer's own
        # cleanup already released the slot (so it's not gated on the release below).
        # The payload's agent_transcript_path (when present) beats the derived path —
        # it also lets a never-started agent's spend fold if its transcript exists.
        rec_st = reconcile_spend(LOG, tpath, agent_id, is_teammate(tpath, agent_id),
                                 d.get("agent_transcript_path") or "")
        got = claude_slots.lookup_id("sub", LOG, agent_id)
        # Release FIRST, emit only if THIS call deleted the row: two overlapping
        # duplicate stops (the dead-streamer case fires them concurrently) could
        # both pass the lookup above and both paint the footer — the atomic
        # DELETE's rowcount (release_id's return) is the once-only licence.
        if got and claude_slots.release_id("sub", LOG, agent_id):
            dur = fmt_dur(time.time() - got[1]) if got[1] else ""
            chip = f"■ {atype} ended · {dur}" if dur else f"■ {atype} ended"
            pal = "team" if is_teammate(tpath, agent_id) else "sub"
            O.emit(LOG, O.rule(), O.label(chip, claude_slots.color(pal, got[0])), O.rule())
            A.hook_event(d, decision="stop: SAFETY NET footer (streamer died mid-run, "
                         "spend " + rec_st + ")")
        elif not started:
            # Not a duplicate — this agent NEVER started here. Name it distinctly:
            # "no transcript" is the scoreboard-under-/cost tell (billed spend the
            # transcript-folding scoreboard structurally cannot see).
            A.hook_event(d, decision="stop: never started (hidden agent) — spend " + rec_st)
        else:
            A.hook_event(d, decision="stop: no-op (already finalised / duplicate stop; "
                         "spend " + rec_st + ")")
        S.agent_set(LOG, agent_id, done=0)        # don't wedge a future resume
        claude_slots.pid_del(LOG, agent_id)
    else:
        A.hook_event(d, decision="stop: done flag set, streamer will finalise")


def entry():
    H.run(main, phase=PHASE)
