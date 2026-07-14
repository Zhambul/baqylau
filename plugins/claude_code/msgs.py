# plugins/claude_code/msgs.py — the agent-team message tracker (the "✉ messages" scoreboard row
# + mirror events). Extracted from claude_ops.py: its only consumer is
# claude-scorebar.py, and claude_ops had become a grab-bag.
import json
import os
import re
import time

from core import paths as P
from core import state as S

from core.noaudit import load_audit

A = load_audit()   # always-on audit trail (CLAUDE_AUDIT=0 disables); inert stub if it can't import

# --- team message tracker (the "✉ messages" scoreboard row + mirror events) -----
# A second, separate scoreboard line rendered ABOVE the session line by
# claude-scorebar.py, plus inbox arrival/read events surfaced in the mirror itself,
# giving live visibility into the agent-team message flow.
#
# There is NO hook for a message being read/consumed (SendMessage is observable via
# PostToolUse, but nothing fires when a teammate drains its inbox), so we can't bump a
# sidecar event-style. Instead the tracker is STATEFUL POLLING: the one scorebar per
# session already scans inboxes each tick, so it diffs the current inbox snapshot
# against the persisted state (claude_state's messages table) keyed by msg_id and folds transitions into
# CUMULATIVE counters — which therefore survive a teammate draining its inbox (the
# whole point; a plain snapshot goes blank the instant a message is consumed).
#
# A message counts as `read` once we observe read:true OR it disappears from the inbox
# (draining implies it was consumed). unread_now = delivered - read. Single writer (the
# lone scorebar), so no flock. Misses transitions that happen entirely while the mirror
# is toggled off — an accepted gap for an ambient visibility aid.


def team_dir(log):
    """The agent-team directory for a mirror log, or None if this isn't a team
    session. The log is /tmp/claude-mirror-<session_id>.log; the team dir is
    ~/.claude/teams/session-<first-8-of-session-id> (see the config.json `name`)."""
    m = re.match(r"([0-9a-fA-F]{8})-", P.sid_from_log(log))
    if not m:
        return None
    d = os.path.expanduser("~/.claude/teams/session-" + m.group(1).lower())
    return d if os.path.isdir(d) else None


STALE_S = 60                    # an unread message sitting longer than this is "stale"


def _msg_epoch(ts):
    """ISO-8601 timestamp (trailing Z) -> epoch seconds, or None if unparseable — an
    unreadable timestamp then never counts as stale, which is the safe way to be wrong."""
    if not ts:
        return None
    from datetime import datetime
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.strptime(ts.replace("Z", "+0000"), fmt).timestamp()
        except Exception:
            pass
    return None


def _scan_inbox(d):
    """Snapshot of every message currently in this team's inboxes, keyed by
    (recipient, msg_id): {key: read_bool}, {key: (from, recipient, summary)}
    (recipient = inbox filename stem), and {key: epoch_or_None}. Keyed per
    RECIPIENT COPY, not per msg_id: a broadcast puts the same msg_id in several
    inboxes, and collapsing those made the tracked read flag whichever copy
    os.listdir yielded last — deliveries undercounted (one event for N copies)
    and reads double-counted or lost as the flag flapped between copies.
    Torn/malformed inbox files are skipped."""
    inbox = os.path.join(d, "inboxes")
    states, meta, ts = {}, {}, {}
    try:
        files = os.listdir(inbox)
    except OSError:
        return states, meta, ts
    for fn in files:
        if not fn.endswith(".json"):
            continue
        recipient = fn[:-5]
        try:
            with open(os.path.join(inbox, fn), encoding="utf-8") as f:
                msgs = json.load(f)
        except Exception:
            continue
        if not isinstance(msgs, list):
            continue
        for m in msgs:
            if not isinstance(m, dict) or m.get("type") != "message":
                continue
            mid = m.get("msg_id")
            if not mid:
                continue
            k = (recipient, mid)
            states[k] = bool(m.get("read"))
            meta[k] = (m.get("from") or "?", recipient, m.get("summary") or "")
            ts[k] = _msg_epoch(m.get("timestamp"))
    return states, meta, ts


def update_messages(log):
    """Stateful team-message tracker. Scans inboxes, diffs against the persisted
    state (claude_state's messages table + cumulative counters — was a .msgs.json
    sidecar) keyed by msg_id, updates the counters, and returns (parts, events):
      parts  — [(kind, text)] census for the ✉ row: msgs / unread / read; always leads
               with a msgs count (0 included) so the row is never blank, even for a
               non-team session.
      events — [(kind, from, recipient, summary)] transitions to surface in the mirror;
               kind is 'new' (just delivered — still unread) or 'read' (consumed).
    Idempotent when nothing changed (a repaint with an unchanged inbox emits no events
    and rewrites nothing), so it is safe to call on every render — incl. resize repaints."""
    d = team_dir(log)
    if not d:
        return [("msgs", "0 msgs")], []      # non-team: still show a 0 count, no events
    delivered0, read0, live = S.msgs_state(log)
    delivered, read = delivered0, read0
    cur, meta, ts = _scan_inbox(d)       # keyed by (recipient, msg_id) — see _scan_inbox
    events = []
    for k, is_read in cur.items():       # deliveries — copies present now we hadn't seen
        if k not in live:
            delivered += 1
            frm, to, summ = meta[k]
            events.append(("new", frm, to, summ))
            if is_read:                  # arrived already read (fast consumer)
                read += 1
                events.append(("read", frm, to, summ))
    for k, ent in list(live.items()):    # reads/drains among copies we were tracking
        was_read = bool(ent[0])
        if k not in cur:                 # drained -> consumed => read
            if not was_read:
                read += 1
                events.append(("read", ent[1], ent[2], ent[3]))
        elif cur[k] and not was_read:    # flipped read:true in place
            read += 1
            frm, to, summ = meta[k]
            events.append(("read", frm, to, summ))
    new_live = {k: [cur[k], meta[k][0], meta[k][1], meta[k][2]] for k in cur}
    if delivered != delivered0 or read != read0 or new_live != live:
        S.msgs_write(log, delivered, read, new_live)
    # Audit message-tracker transitions (only when something actually changed —
    # this runs on every scorebar tick). One row per delivery/read event plus the
    # resulting cumulative counters, so a wrong ✉ census is traceable.
    if events:
        A.state_file(log, S.db_path(log), "msg-transitions", {
            "events": [{"kind": k, "from": f_, "to": t, "summary": s}
                       for k, f_, t, s in events],
            "now": {"delivered": delivered, "read": read}})
    # `stale` is a CURRENT-STATE count (unlike the cumulative delivered/read): messages
    # sitting unread in an inbox right now for longer than STALE_S. It's a DISJOINT group
    # from `unread` — the currently-pending messages split into fresh (unread) vs stale —
    # so unread + stale = delivered - read. A stale message is the age-only signal for a
    # possibly-dead recipient (there's no liveness flag in the team files to know for sure).
    now = time.time()
    stale = sum(1 for k, is_read in cur.items()
                if not is_read and ts.get(k) and now - ts[k] > STALE_S)
    # Always lead with the delivered count (0 included) so the ✉ row is never blank; the
    # renderer shows this even for non-team sessions (which returned early above with the
    # same 0-count shape). unread/stale/read segments appear only when non-zero.
    unread_now = max(0, delivered - read)
    stale = min(stale, unread_now)                   # never exceed the derived pending count
    fresh = unread_now - stale
    parts = [("msgs", f"{delivered} msg" + ("s" if delivered != 1 else ""))]
    if fresh:
        parts.append(("unread", f"{fresh}● unread"))
    if stale:
        parts.append(("stale", f"{stale}◐ stale"))
    if read:
        parts.append(("read", f"{read}◉ read"))
    return parts, events
