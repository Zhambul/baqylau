#!/usr/bin/env python3
# claude-codex-stream.py MIRROR_LOG "r,g,b" SRCFILE JSONFILE LABEL
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
#
# The colour is passed in as "r,g,b" (the watcher round-robins claude_slots.CODEX_
# PALETTE) — this stream keeps no slot marker, so it never affects the tab colour.
# A codex run is attributed to the SESSION / cwd, not the launching agent_id, so it
# reads as its own top-level stream (rule-bracketed) in the codex palette.
import json, os, re, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import claude_render as R
import claude_ops as O

LOG      = sys.argv[1] if len(sys.argv) > 1 else ""
SLOT_RGB = tuple(int(x) for x in sys.argv[2].split(",")) if len(sys.argv) > 2 else (0, 200, 150)
LOGFILE  = sys.argv[3] if len(sys.argv) > 3 else ""
JSONF    = sys.argv[4] if len(sys.argv) > 4 else "-"
LABEL    = sys.argv[5] if len(sys.argv) > 5 else "task"
ROLLOUT  = LOGFILE.endswith(".jsonl")     # else companion .log
RST, FAIL = R.RST, R.fg(224, 108, 117)

# A companion job-log line is prefixed with an ISO timestamp; the tail is the event
# head. Un-prefixed lines are continuation body of the preceding block event.
TS = re.compile(r"^\[\d{4}-\d\d-\d\dT[\d:.]+Z\]\s?(.*)$")


def chip(glyph, kind):
    return O.label(f"codex {glyph} {kind}", SLOT_RGB)


def gutter(text):
    return O.gut(R.unescape(text), SLOT_RGB)


def dim_gut(text):
    return O.gut(R.DIM + R.unescape(text) + RST, SLOT_RGB)


def cap(text, n):
    lines = text.split("\n")
    if len(lines) <= n:
        return text
    more = len(lines) - n
    return "\n".join(lines[:n]) + f"\n… ({more} more line{'s' if more != 1 else ''})"


_last_msg = ""            # last assistant-message body, to de-dup a repeated "Final output"


# --- companion (.log) parse: the pre-digested `[ts] …` activity stream --------------
def render_record(head, body):
    global _last_msg
    head = (head or "").rstrip()
    if not head or head.startswith("Assistant message captured:"):
        return
    if head.startswith(("Thread ready", "Turn started", "Turn completed",
                        "Starting Codex", "Queued", "Reviewer finished")):
        return
    if head.startswith("Running command:"):
        O.emit(LOG, chip("▶", "cmd"), O.code(head[len("Running command:"):].strip()))
        return
    if head.startswith(("Command completed:", "Command failed:")):
        m = re.search(r"\(exit (\d+)\)", head)
        if m and m.group(1) != "0":
            O.emit(LOG, O.gut(FAIL + "■ exit " + m.group(1) + RST, SLOT_RGB))
        return
    if head.startswith("Reviewer started"):
        what = head.split(":", 1)[-1].strip() if ":" in head else head
        O.emit(LOG, chip("◆", "review"), gutter(cap(what, 4)))
        return
    body_text = "\n".join(body).strip()
    if head == "Assistant message":
        if body_text:
            _last_msg = body_text
            O.emit(LOG, chip("✎", "message"), gutter(cap(body_text, 40)))
        return
    if head == "Reasoning summary":
        if body_text:
            O.emit(LOG, chip("⋯", "reasoning"), dim_gut(cap(body_text, 16)))
        return
    if head == "Review output":
        if body_text:
            O.emit(LOG, chip("⇠", "review"), gutter(cap(body_text, 80)))
        return
    if head == "Final output":
        if body_text and body_text != _last_msg:
            O.emit(LOG, chip("⇠", "result"), gutter(cap(body_text, 80)))
        return
    if head.startswith("Subagent "):
        O.emit(LOG, chip("✎", "sub"), gutter(cap(body_text or head, 20)))
        return
    O.emit(LOG, dim_gut(cap(head, 4)))


_cur_head, _cur_body = None, []


def feed_line(line):
    global _cur_head, _cur_body
    m = TS.match(line)
    if m:
        if _cur_head is not None:
            render_record(_cur_head, _cur_body)
        _cur_head, _cur_body = m.group(1), []
    elif line.strip():
        _cur_body.append(line)


def read_status():
    try:
        with open(JSONF, encoding="utf-8") as fh:
            return (json.load(fh).get("status") or "").strip()
    except Exception:
        return ""


# --- rollout (.jsonl) parse: codex's own native session log -------------------------
_ro_started = _ro_completed = _ro_done_wall = None
_ro_active = False
_ro_aborted = False


def feed_rollout(o):
    global _last_msg, _ro_started, _ro_completed, _ro_done_wall, _ro_active, _ro_aborted
    t = o.get("type")
    p = o.get("payload") or {}
    if t == "event_msg":
        st = p.get("type")
        if st == "task_started":
            _ro_active = True
            if _ro_started is None:
                _ro_started = p.get("started_at")
        elif st == "task_complete":
            _ro_active = False
            _ro_completed = p.get("completed_at") or _ro_completed
            _ro_done_wall = time.time()
        elif st == "turn_aborted":
            _ro_active, _ro_aborted, _ro_done_wall = False, True, time.time()
        elif st == "user_message":
            msg = (p.get("message") or "").strip()
            if msg:
                O.emit(LOG, chip("⇢", "prompt"), gutter(cap(msg, 6)))
        elif st == "agent_reasoning":
            txt = (p.get("text") or "").strip()
            if txt:
                O.emit(LOG, chip("⋯", "reasoning"), dim_gut(cap(txt, 12)))
        elif st == "agent_message":
            msg = (p.get("message") or "").strip()
            if msg:
                _last_msg = msg
                O.emit(LOG, chip("✎", "message"), gutter(cap(msg, 40)))
    elif t == "response_item" and p.get("type") == "function_call" and p.get("name") == "exec_command":
        try:
            args = json.loads(p.get("arguments") or "{}")
        except Exception:
            args = {}
        cmd = args.get("cmd") or args.get("command") or ""
        if isinstance(cmd, list):
            cmd = " ".join(str(x) for x in cmd)
        if cmd:
            O.emit(LOG, chip("▶", "cmd"), O.code(cmd))


def main():
    if not (LOG and LOGFILE):
        return
    start = time.time()
    # Wait for the source to appear (a companion .log lands a beat after its sidecar).
    while not os.path.exists(LOGFILE) and time.time() < start + 15 and os.path.exists(LOG):
        time.sleep(0.2)
    if not os.path.exists(LOGFILE):
        return

    O.emit(LOG, O.rule(), O.label("codex ▶ " + LABEL, SLOT_RGB), O.rule())

    pos, pending = 0, b""

    def pump():
        nonlocal pos, pending
        try:
            size = os.path.getsize(LOGFILE)
        except OSError:
            return
        if size <= pos:
            return
        try:
            with open(LOGFILE, "rb") as fh:
                fh.seek(pos); pending += fh.read(); pos = size
        except OSError:
            return
        *lines, pending = pending.split(b"\n")
        for ln in lines:
            s = ln.decode("utf-8", "replace")
            if ROLLOUT:
                s = s.strip()
                if s:
                    try:
                        feed_rollout(json.loads(s))
                    except Exception:
                        pass
            else:
                feed_line(s)

    GRACE = 8.0        # rollout: close the block if no new turn starts within grace
    while True:
        pump()
        if not os.path.exists(LOG):          # session ended -> stop
            break
        if ROLLOUT:
            if _ro_done_wall and not _ro_active and (time.time() - _ro_done_wall) >= GRACE:
                pump(); break
        elif read_status() in ("completed", "failed", "cancelled"):
            time.sleep(0.2); pump(); pump()  # drain the tail
            break
        if time.time() - start > 6 * 3600:   # backstop for a stuck run
            break
        time.sleep(0.4)

    if not ROLLOUT and _cur_head is not None:
        render_record(_cur_head, _cur_body)

    if ROLLOUT:
        state = "failed" if _ro_aborted else "ended"
        sec = (_ro_completed - _ro_started) if (_ro_started and _ro_completed) \
            else max(0.0, time.time() - start)
    else:
        state = "failed" if read_status() == "failed" else "ended"
        sec = max(0.0, time.time() - start)
    dur = f"{sec:.1f}s" if sec < 60 else f"{int(sec // 60)}m{int(sec % 60):02d}s"
    O.emit(LOG, O.rule(), O.label(f"■ codex {LABEL} {state} · {dur}", SLOT_RGB), O.rule())


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
