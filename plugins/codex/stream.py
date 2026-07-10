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
#
# The colour is passed in as "r,g,b" (the watcher round-robins claude_slots.CODEX_
# PALETTE) — this stream keeps no slot marker, so it never affects the tab colour.
# A codex run is attributed to the SESSION / cwd, not the launching agent_id, so it
# reads as its own top-level stream (rule-bracketed) in the codex palette.
import json, os, re, sys, time

from core import ops as O
from core import render as R
from core import state as S
from core import tail as T

A = O.A    # audit trail (real module, or a no-op stub if it failed to import)

LOG      = sys.argv[1] if len(sys.argv) > 1 else ""
SLOT_RGB = tuple(int(x) for x in sys.argv[2].split(",")) if len(sys.argv) > 2 else (0, 200, 150)
LOGFILE  = sys.argv[3] if len(sys.argv) > 3 else ""
JSONF    = sys.argv[4] if len(sys.argv) > 4 else "-"
LABEL    = sys.argv[5] if len(sys.argv) > 5 else "task"
ROLLOUT  = LOGFILE.endswith(".jsonl")     # else companion .log
RST, FAIL = R.RST, R.fg(*O.RED)

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


def _patch_delta(ch):
    """(added, removed) line counts for one patch_apply_end change entry."""
    t = ch.get("type")
    if t == "add":
        return len((ch.get("content") or "").splitlines()), 0
    if t == "delete":
        return 0, len((ch.get("content") or "").splitlines())
    add = rem = 0
    for ln in (ch.get("unified_diff") or "").splitlines():
        if ln.startswith("+") and not ln.startswith("+++"):
            add += 1
        elif ln.startswith("-") and not ln.startswith("---"):
            rem += 1
    return add, rem


def render_patch(p):
    """patch_apply_end: the authoritative file-op record for a codex run — it
    carries the RESOLVED absolute paths + per-file diffs (the apply_patch
    response_item only has repo-relative patch text, so that one is ignored:
    rendering both would duplicate). One file-op line per changed file + the
    same scoreboard accounting the substream does for subagent file ops
    (unique-path files set, ± line sums, Edit/Write tool tallies) — plain
    bump() rows, no meta: these are file/line deltas, not the token/cost
    deltas the unattributed-bump anomaly guards."""
    if not p.get("success"):
        O.emit(LOG, O.gut(FAIL + "■ patch failed" + RST, SLOT_RGB))
        return
    for path, ch in (p.get("changes") or {}).items():
        if not isinstance(ch, dict):
            continue
        verb, rgb, tool = FILE_VERB.get(ch.get("type"), FILE_VERB["update"])
        name = os.path.basename((path or "").rstrip("/")) or path or "?"
        add, rem = _patch_delta(ch)
        line = (R.fg(*rgb) + verb + R.DIM + "(" + R.COL["def"] + name
                + R.DIM + ")" + RST)
        parts = []
        if add:
            parts.append(R.fg(*O.GREEN) + "+%d" % add + RST)
        if rem:
            parts.append(R.fg(*O.RED) + "-%d" % rem + RST)
        if parts:
            line += "  " + " ".join(parts)
        O.emit(LOG, O.gut(line, SLOT_RGB))
        O.bump(LOG, tool=tool, file=path, added=add, removed=rem)

# A companion job-log line is prefixed with an ISO timestamp; the tail is the event
# head. Un-prefixed lines are continuation body of the preceding block event.
TS = re.compile(r"^\[\d{4}-\d\d-\d\dT[\d:.]+Z\]\s?(.*)$")


def chip(glyph, kind, g=None, lk=None):
    # g + lk tie this block's header to its code/gut body for the ⧉ copy handler —
    # a fresh O.new_group() per block (codex records carry no tool_use_id). Same
    # affordance the claude-session mirror paints (core/copy.py).
    return O.label(f"codex {glyph} {kind}", SLOT_RGB, g=g, lk=lk)


def gutter(text, g=None):
    return O.gut(R.unescape(text), SLOT_RGB, g=g)


def dim_gut(text, g=None):
    return O.gut(R.DIM + R.unescape(text) + RST, SLOT_RGB, g=g)


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
        g = O.new_group(LOG)
        # cmd-only link: codex's exit-code output lands in a separate record, not this
        # group, so there's no ⧉out body to offer.
        O.emit(LOG, chip("▶", "cmd", g=g, lk=[["cmd", "⧉cmd"]]),
               O.code(head[len("Running command:"):].strip(), g=g))
        return
    if head.startswith(("Command completed:", "Command failed:")):
        m = re.search(r"\(exit (\d+)\)", head)
        if m and m.group(1) != "0":
            O.emit(LOG, O.gut(FAIL + "■ exit " + m.group(1) + RST, SLOT_RGB))
        return
    if head.startswith("Reviewer started"):
        what = head.split(":", 1)[-1].strip() if ":" in head else head
        g = O.new_group(LOG)
        O.emit(LOG, chip("◆", "review", g=g, lk=O.COPY_ALL), gutter(cap(what, 4), g=g))
        return
    body_text = "\n".join(body).strip()
    if head == "Assistant message":
        if body_text:
            _last_msg = body_text
            g = O.new_group(LOG)
            O.emit(LOG, chip("✎", "message", g=g, lk=O.COPY_ALL),
                   gutter(cap(body_text, 40), g=g))
        return
    if head == "Reasoning summary":
        if body_text:
            g = O.new_group(LOG)
            O.emit(LOG, chip("⋯", "reasoning", g=g, lk=O.COPY_ALL),
                   dim_gut(cap(body_text, 16), g=g))
        return
    if head == "Review output":
        if body_text:
            g = O.new_group(LOG)
            O.emit(LOG, chip("⇠", "review", g=g, lk=O.COPY_ALL),
                   gutter(cap(body_text, 80), g=g))
        return
    if head == "Final output":
        if body_text and body_text != _last_msg:
            g = O.new_group(LOG)
            O.emit(LOG, chip("⇠", "result", g=g, lk=O.COPY_ALL),
                   gutter(cap(body_text, 80), g=g))
        return
    if head.startswith("Subagent "):
        g = O.new_group(LOG)
        O.emit(LOG, chip("✎", "sub", g=g, lk=O.COPY_ALL),
               gutter(cap(body_text or head, 20), g=g))
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
_ro_model = ""            # bare model id from turn_context — prices the footer
_ro_tag = ""              # "model · effort" chip last shown (re-shown on change)
_ro_usage = None          # CUMULATIVE total_token_usage from the last token_count


def feed_rollout(o):
    global _last_msg, _ro_started, _ro_completed, _ro_done_wall, _ro_active, \
        _ro_aborted, _ro_model, _ro_tag, _ro_usage
    t = o.get("type")
    p = o.get("payload") or {}
    if t == "turn_context":
        # Model + effort for this turn — shown once (dim ⚙ line) and re-shown
        # only when it changes; the bare model id prices the footer rollup.
        model = (p.get("model") or "").strip()
        eff = (((p.get("collaboration_mode") or {}).get("settings") or {})
               .get("reasoning_effort") or "").strip()
        tag = model + (" · " + eff if eff else "")
        if model and tag != _ro_tag:
            _ro_model, _ro_tag = model, tag
            O.emit(LOG, dim_gut("⚙ " + tag))
        return
    if t == "event_msg":
        st = p.get("type")
        if st == "token_count":
            # Cumulative usage snapshot (info is null on rate-limit-only
            # events). Folded into the scoreboard ONCE, at the footer — the
            # totals are cumulative, so summing per-event would double-count.
            u = (p.get("info") or {}).get("total_token_usage") if isinstance(
                p.get("info"), dict) else None
            if isinstance(u, dict):
                _ro_usage = u
        elif st == "patch_apply_end":
            render_patch(p)
        elif st == "context_compacted":
            # Same ⟳ treatment the substream gives a compact_boundary, so a
            # gap in a codex run's history reads the same way.
            O.emit(LOG, O.gut(R.fg(*O.YELLOW) + "⟳ compacted" + RST, SLOT_RGB))
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
                g = O.new_group(LOG)
                O.emit(LOG, chip("⇢", "prompt", g=g, lk=O.COPY_ALL),
                       gutter(cap(msg, 6), g=g))
        elif st == "agent_reasoning":
            txt = (p.get("text") or "").strip()
            if txt:
                g = O.new_group(LOG)
                O.emit(LOG, chip("⋯", "reasoning", g=g, lk=O.COPY_ALL),
                       dim_gut(cap(txt, 12), g=g))
        elif st == "agent_message":
            msg = (p.get("message") or "").strip()
            if msg:
                _last_msg = msg
                g = O.new_group(LOG)
                O.emit(LOG, chip("✎", "message", g=g, lk=O.COPY_ALL),
                       gutter(cap(msg, 40), g=g))
    elif t == "response_item" and p.get("type") == "web_search_call":
        q = (p.get("action") or {}).get("query") or ""
        if q:
            g = O.new_group(LOG)
            O.emit(LOG, chip("⌕", "search", g=g, lk=O.COPY_ALL), gutter(cap(q, 4), g=g))
    elif t == "response_item" and p.get("type") == "function_call_output":
        # The exec output record: surface a FAILED exit prominently (the
        # companion path already does this from its "Command failed" lines).
        out = p.get("output") or ""
        m = re.search(r"(?:^|\n)(?:Exit code|Process exited with code)[: ]+(\d+)",
                      out[:300])
        if m and m.group(1) != "0":
            O.emit(LOG, O.gut(FAIL + "■ exit " + m.group(1) + RST, SLOT_RGB))
    elif t == "response_item" and p.get("type") == "function_call" and p.get("name") == "exec_command":
        try:
            args = json.loads(p.get("arguments") or "{}")
        except Exception:
            args = {}
        cmd = args.get("cmd") or args.get("command") or ""
        if isinstance(cmd, list):
            cmd = " ".join(str(x) for x in cmd)
        if cmd:
            g = O.new_group(LOG)
            O.emit(LOG, chip("▶", "cmd", g=g, lk=[["cmd", "⧉cmd"]]), O.code(cmd, g=g))


def main(run):
    if not (LOG and LOGFILE):
        return
    start = time.time()
    # Wait for the source to appear (a companion .log lands a beat after its sidecar).
    if not T.wait_for(LOGFILE, start + 15,
                      alive=lambda: os.path.exists(S.db_path(LOG))):
        run.end("src-never-appeared")
        return

    # Re-check right before the first emit: SessionEnd may have parked the state
    # DB during the wait above, and claude_state's connect would CREATE a missing
    # DB file — resurrecting the very file whose existence is the session-alive
    # signal (the codex watcher's own loop polls it and would then never exit).
    if not os.path.exists(S.db_path(LOG)):
        run.end("state-db-parked (before header)")
        return
    O.emit(LOG, O.rule(), O.label("codex ▶ " + LABEL, SLOT_RGB), O.rule())

    tail = T.FileTailer(LOGFILE)

    def pump():
        for ln in (tail.pump() or ()):
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

    # rollout: close the block if no new turn starts within grace. Env override
    # exists solely for the test suite (README § Testing).
    GRACE = float(os.environ.get("CLAUDE_CODEX_GRACE_S") or 8.0)
    while True:
        pump()
        if not os.path.exists(S.db_path(LOG)):   # session ended (state DB parked) -> stop
            run.end("state-db-parked (session end)")
            # No footer: writing it would go into the parked *.keep snapshot via
            # the cached connection — or recreate the DB file outright.
            return
        if ROLLOUT:
            if _ro_done_wall and not _ro_active and (time.time() - _ro_done_wall) >= GRACE:
                pump(); run.end("task-complete"); break
        elif read_status() in ("completed", "failed", "cancelled"):
            time.sleep(0.2); pump(); pump()  # drain the tail
            run.end("sidecar-status: " + read_status())
            break
        if time.time() - start > T.BACKSTOP_S:   # backstop for a stuck run
            run.end("backstop-timeout")
            break
        time.sleep(T.POLL_S)

    if not ROLLOUT and _cur_head is not None:
        render_record(_cur_head, _cur_body)

    if ROLLOUT:
        state = "failed" if _ro_aborted else "ended"
        sec = (_ro_completed - _ro_started) if (_ro_started and _ro_completed) \
            else max(0.0, time.time() - start)
    else:
        state = "failed" if read_status() == "failed" else "ended"
        sec = max(0.0, time.time() - start)
    dur = O.fmt_dur(sec)
    foot = f"■ codex {LABEL} {state} · {dur}"
    if ROLLOUT and isinstance(_ro_usage, dict):
        # Cumulative rollup from the run's last token_count: fresh billed
        # input (input minus cached) / generated output / cache-hit share —
        # the same figures a subagent footer shows, so runs compare at a
        # glance. Folded into the session scoreboard ONCE here (bump-agent —
        # the meta carries agent kind/model + the split, so the Σ row and cost
        # are re-derivable from the audit DB alone). No fold on the parked-DB
        # exit above, and none for companion (.log) runs — their usage isn't
        # in the activity log (their rollout is deliberately not adopted).
        tin = int(_ro_usage.get("input_tokens") or 0)
        tcache = int(_ro_usage.get("cached_input_tokens") or 0)
        tout = int(_ro_usage.get("output_tokens") or 0)
        fresh = max(tin - tcache, 0)
        if fresh or tout:
            foot += f" · {O.kfmt(fresh)} in · {O.kfmt(tout)} out"
            if tin > 0:
                foot += f" · cache {tcache * 100 // tin}%"
        usd = codex_cost_usd(_ro_model, fresh, tout, tcache)
        if usd:
            foot += " · ≈ " + O.fmt_usd(usd)
        deltas = {}
        if usd:
            deltas["cost"] = usd
        if fresh or tout:
            deltas["tokens"] = fresh + tout
        if fresh or tout or tcache:
            deltas["tk_in"] = fresh
            deltas["tk_out"] = tout
            deltas["tk_read"] = tcache
        if deltas:
            O.bump(LOG, meta={"agent_id": "", "kind": "codex",
                              "model": _ro_model, "in": fresh, "out": tout,
                              "cache": tcache, "create": 0, "src": LOGFILE,
                              "label": LABEL}, **deltas)
    O.emit(LOG, O.rule(), O.label(foot, SLOT_RGB), O.rule())


def entry():
    with T.stream_lifecycle(LOG, "codex", task_id=LABEL, src_path=LOGFILE,
                            ctx={"src": LOGFILE, "label": LABEL}) as run:
        main(run)
