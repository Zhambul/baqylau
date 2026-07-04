#!/usr/bin/env python3
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
import json, os, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import claude_slots
import claude_ops as O
import claude_state as S

A = O.A    # audit trail (real module, or a no-op stub if it failed to import)

PHASE = sys.argv[1] if len(sys.argv) > 1 else "start"
LOG   = ""   # set in main() from the payload's session_id (per-session log)
HERE  = os.path.dirname(os.path.abspath(__file__))


def fmt_dur(sec):
    if sec <= 0:
        return ""
    return f"{sec:.1f}s" if sec < 60 else f"{int(sec // 60)}m{int(sec % 60):02d}s"


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


def main():
    global LOG
    try:
        d = json.load(sys.stdin)
    except Exception:
        A.error("", "payload parse (stdin not valid JSON)")
        return
    LOG = O.log_path(d)

    # PreToolUse(Agent): stash the task description for the upcoming SubagentStart.
    if PHASE == "push":
        ti = d.get("tool_input") or {}
        claude_slots.desc_push(LOG, ti.get("description") or "")
        A.hook_event(d, decision="pushed description: " + (ti.get("description") or ""))
        return

    agent_id = d.get("agent_id")
    if not agent_id:
        A.hook_event(d, decision="ignored: no agent_id")
        return
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
        streamer = os.path.join(HERE, "claude-substream.py")
        spawned = None
        if tpath and os.path.exists(streamer):
            try:
                proc = subprocess.Popen(
                    [sys.executable, streamer, agent_id, tpath, LOG, str(slot), atype, pal, desc],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, start_new_session=True)
                claude_slots.pid_set(LOG, agent_id, proc.pid)
                spawned = proc.pid
                A.spawn(LOG, proc.pid, [streamer, agent_id, str(slot), atype, pal],
                        purpose=f"stream:{'teammate' if team else 'subagent'} {atype}")
            except Exception:
                A.error(LOG, "spawn substream", {"agent": agent_id, "type": atype})
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
        try:
            subprocess.run([os.path.join(HERE, "claude-tab-status.py"), "agent-start", LOG],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=10)
        except Exception:
            pass
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
    S.agent_set(LOG, agent_id, done=1)
    A.state_file(LOG, "state:agent." + agent_id, "write", "done=1 (stop signal for streamer)")
    _p = claude_slots.pid_get(LOG, agent_id)
    running = bool(_p) and alive(_p)
    if not running:
        got = claude_slots.lookup_id("sub", LOG, agent_id)
        if got:                                   # streamer died without finalising
            dur = fmt_dur(time.time() - got[1]) if got[1] else ""
            chip = f"■ {atype} ended · {dur}" if dur else f"■ {atype} ended"
            pal = "team" if is_teammate(tpath, agent_id) else "sub"
            O.emit(LOG, O.rule(), O.label(chip, claude_slots.color(pal, got[0])), O.rule())
            claude_slots.release_id("sub", LOG, agent_id)
            A.hook_event(d, decision="stop: SAFETY NET footer (streamer died mid-run)")
        else:
            A.hook_event(d, decision="stop: no-op (already finalised / duplicate stop)")
        S.agent_set(LOG, agent_id, done=0)        # don't wedge a future resume
        claude_slots.pid_del(LOG, agent_id)
    else:
        A.hook_event(d, decision="stop: done flag set, streamer will finalise")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        A.error(LOG, "main", {"phase": PHASE})
