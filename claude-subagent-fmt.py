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

PHASE = sys.argv[1] if len(sys.argv) > 1 else "start"
LOG   = ""   # set in main() from the payload's session_id (per-session log)
HERE  = os.path.dirname(os.path.abspath(__file__))


def fmt_dur(sec):
    if sec <= 0:
        return ""
    return f"{sec:.1f}s" if sec < 60 else f"{int(sec // 60)}m{int(sec % 60):02d}s"


def pid_path(agent_id):
    return os.path.join(LOG + ".slots", f"sub.pid.{agent_id}")


def is_teammate(tpath, agent_id):
    # An in-process agent-team teammate is, at the storage layer, just a subagent —
    # but its meta.json carries taskKind == "in_process_teammate". (Unlike an ordinary
    # subagent's meta, a teammate's is present at SubagentStart, so reading it here is
    # reliable.) Teammates render in the lighter "team" palette + a "teammate" header.
    try:
        base = tpath[:-6] if tpath.endswith(".jsonl") else tpath
        meta = os.path.join(base, "subagents", f"agent-{agent_id}.meta.json")
        with open(meta, encoding="utf-8") as f:
            return json.load(f).get("taskKind") == "in_process_teammate"
    except Exception:
        return False


def alive(pid):
    try:
        os.kill(pid, 0); return True
    except OSError:
        return False


def main():
    global LOG
    try:
        d = json.load(sys.stdin)
    except Exception:
        return
    LOG = O.log_path(d)

    # PreToolUse(Agent): stash the task description for the upcoming SubagentStart.
    if PHASE == "push":
        ti = d.get("tool_input") or {}
        claude_slots.desc_push(LOG, ti.get("description") or "")
        return

    agent_id = d.get("agent_id")
    if not agent_id:
        return
    atype = d.get("agent_type") or "agent"
    tpath = d.get("transcript_path") or ""

    if PHASE == "start":
        # Teammates reuse the subagent slot machinery (claim_id "sub" + sub.* markers,
        # so bg_command_running keeps the tab blue while one runs); only the colour
        # palette + header keyword differ.
        team = is_teammate(tpath, agent_id)
        pal  = "team" if team else "sub"
        slot, is_new = claude_slots.claim_id("sub", LOG, agent_id)
        # A background agent (and a teammate in particular) can fire SubagentStart
        # MORE THAN ONCE. If we already claimed this agent's slot and its streamer is
        # still live, this is a duplicate start — don't write a second header or spawn
        # a second streamer (which would re-render the whole transcript a second time).
        if not is_new:
            try:
                if alive(int(open(pid_path(agent_id)).read().strip())):
                    return
            except Exception:
                pass
        rgb = claude_slots.color(pal, slot)
        desc = claude_slots.desc_pop(LOG)
        if team:
            head = f"▶ {atype} · teammate · {desc}" if desc else f"▶ {atype} · teammate"
        else:
            head = f"▶ {atype} · {desc}" if desc else f"▶ {atype}"
        O.emit(LOG, O.blank(), O.rule(), O.label(head, rgb), O.rule())
        # Spawn the transcript streamer (detached) and record its pid so `stop` can
        # tell whether it's still running. PALETTE (argv 6) tells it which colour
        # family to use — must match the header colour chosen just above.
        streamer = os.path.join(HERE, "claude-substream.py")
        if tpath and os.path.exists(streamer):
            try:
                proc = subprocess.Popen(
                    [sys.executable, streamer, agent_id, tpath, LOG, str(slot), atype, pal],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, start_new_session=True)
                with open(pid_path(agent_id), "w") as f:
                    f.write(str(proc.pid))
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
    slots_dir = LOG + ".slots"
    try:
        os.makedirs(slots_dir, exist_ok=True)
        open(os.path.join(slots_dir, f"sub.done.{agent_id}"), "a").close()
    except Exception:
        pass
    try:
        running = alive(int(open(pid_path(agent_id)).read().strip()))
    except Exception:
        running = False
    if not running:
        got = claude_slots.lookup_id("sub", LOG, agent_id)
        if got:                                   # streamer died without finalising
            dur = fmt_dur(time.time() - got[1]) if got[1] else ""
            chip = f"■ {atype} ended · {dur}" if dur else f"■ {atype} ended"
            pal = "team" if is_teammate(tpath, agent_id) else "sub"
            O.emit(LOG, O.rule(), O.label(chip, claude_slots.color(pal, got[0])), O.rule())
            claude_slots.release_id("sub", LOG, agent_id)
        for p in (os.path.join(LOG + ".slots", f"sub.done.{agent_id}"), pid_path(agent_id)):
            try:
                os.remove(p)
            except Exception:
                pass


if __name__ == "__main__":
    main()
