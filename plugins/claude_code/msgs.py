# plugins/claude_code/msgs.py — the agent-team message tracker (the "✉ messages" scoreboard row
# + mirror events). Extracted from claude_ops.py: its only consumer is
# claude-scorebar.py, and claude_ops had become a grab-bag.
import json
import os
import re
import time

from core import ops as O
from core import paths as P
from core import state as S
from core import streamfmt as SF

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
# against the persisted state (core.state's messages table) keyed by msg_id and folds transitions into
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

# --- the MIRROR-EVENT vocabulary (this module owns it) ------------------------
# Team mail surfaced in the mirror itself: a chip per arrival/read, plus the
# arrival's MESSAGE as a gutter body. It lives HERE rather than in the scorebar
# that paints it, because the web mirror has to read it back — a paint op carries
# no "this is mail" fact, so dashboard/opshtml/actclass.py recovers the `mail`
# activity class from exactly these glyphs and colours (the classifier can't
# import bin/claude-scorebar.py: entry scripts are un-importable by design, which
# is how the ◉ below came to be read as a MONITOR block and counted as one).
#
# The colours match the ●/◉ glyphs the census row uses, so the two surfaces read
# as one system: a delivered/unread message is yellow, a read one green.
MSG_NEW_RGB  = O.YELLOW
MSG_READ_RGB = O.GREEN
GLYPH_SENT = "✉"                # a message SENT — the real thing, with its text
GLYPH_NEW  = "●"                # …the poller seeing it land in an inbox
GLYPH_READ = "◉"                # …and consumed. NOTE: shared with a monitor
#                                 block's chip, so the classifier disambiguates
#                                 by colour (semantic == mail, slot palette ==
#                                 monitor), exactly as it does for ▶.
READ_PREFIX = GLYPH_READ + " read · "

# TWO KINDS OF MAIL ROW, and the split is the whole point (docs/dashboard.md,
# *Team mail*). The MESSAGE row is written at SEND time by the SendMessage hook
# (mail_fmt.py) and carries the text; the DELIVERY/READ rows come from this
# module's inbox poller and carry nothing but the transition. The poller cannot
# be the message: it only sees mail still sitting unread at a tick, which in one
# reviewed session was 12 of the 33 messages sent (10 of those 12 lifecycle
# frames) — a message consumed between ticks left no row at all. So the poller's
# rows are labelled `Mail …` PLUMBING (verbose only on the web) and the hook's
# row is the message.
#
# The WEB WORDING of every one of them (core/ops.py's `note`): one quiet `⏺ …`
# line in the register of a collapsed run's summary, instead of the pane's
# coloured chip. Written HERE, by the producer, for the same reason the glyphs
# are — the presenter must not parse a chip back apart to reword it. `:`
# introduces the message's own words, `·` appends a state, matching the agent
# notes' `finished · 21m 16s`.
def note_message(frm, to, summ=""):
    """`Message <frm> → <to>[: <summary>]` — a real message, whose body is its text.
    The summary is Claude Code's own 5-10 word preview (SendMessage's `summary`)."""
    line = "Message %s → %s" % (frm, to)
    return (line + ": " + summ) if summ else line


def note_mail(frm, to, state):
    """`Mail <frm> → <to> · delivered|read|idle|…` — a poller row. The leading `Mail`
    is the LABEL the reader asked for: it says this line is the mail system talking
    about a message, not the message. `Message` is reserved for the row that carries
    words, so a line can never promise content it does not have."""
    return "Mail %s → %s · %s" % (frm, to, state)


# --- LIFECYCLE FRAMES -------------------------------------------------------
# Most of a team session's mail is not prose: Claude Code delivers teammate
# lifecycle events through the SAME inboxes, as a mailbox record whose `text` is a
# JSON frame — `{"type":"idle_notification","from":"rev-ui-util","idleReason":
# "available"}`. Twelve of the fourteen arrivals in one reviewed lead session were
# these. They carry no `summary`, so the row used to paint no body at all and read
# as `Message rev-ui-util → team-lead` with nothing behind the click ("why can't I
# read the message itself?" — there was no message). Painting the JSON instead
# would be worse: a reader wants the event, not the wire format.
#
# So a frame is WORDED and the JSON is dropped. The type vocabulary is Claude
# Code's own (2.1.220 rejects exactly this list from a plain-text SendMessage:
# "message text must not be a teammate lifecycle/task frame"); an unknown type
# still gets a line — its own `type`, which is at least the truth.
FRAME_PHRASE = {
    "idle_notification":   "idle",
    "task_assignment":     "task assigned",
    "task_completed":      "task completed",
    "teammate_terminated": "terminated",
    "shutdown_rejected":   "shutdown refused",
    "permission_request":  "permission asked",
}
# The frame fields that hold a SENTENCE (per type: an idle frame's wrap-up, a task
# assignment's brief, a termination's reason) — the first one present becomes the
# block's body, so a frame that does say something is still readable. Everything
# else in the frame is machine bookkeeping and is dropped.
FRAME_TEXT = ("summary", "description", "message", "taskSubject", "reason",
              "failureReason")


def frame(text):
    """The lifecycle frame a mailbox record's `text` carries, or None when it is a
    plain message. A frame is a JSON object with a string `type`; anything else —
    prose, a bare list, malformed JSON — is not one, and prose that happens to
    parse as such an object does not occur (Claude Code refuses to SEND a frame as
    plain text, which is where this vocabulary is written down)."""
    t = (text or "").strip()
    if not t.startswith("{"):
        return None                     # the common case, before paying for a parse
    try:
        o = json.loads(t)
    except Exception:
        return None
    return o if isinstance(o, dict) and isinstance(o.get("type"), str) else None


def frame_words(fr):
    """A frame -> (phrase, body): `idle`/`idle (interrupted)`/`task assigned`… and
    the one sentence it carries, if any. One builder for both surfaces, so the
    pane's chip and the web's note cannot describe the same frame differently."""
    kind = fr.get("type") or ""
    phrase = FRAME_PHRASE.get(kind, kind)
    reason = fr.get("idleReason") or fr.get("completedStatus")
    if reason and reason not in ("available", "resolved"):
        phrase += " (%s)" % reason      # …only when it is not the ordinary outcome
    for f in FRAME_TEXT:
        v = fr.get(f)
        if isinstance(v, str) and v.strip():
            return phrase, v.strip()
    return phrase, ""


# How much of a MESSAGE the mail line paints. Deliberately its own ceiling and NOT
# substream_render's CAP_TEAMMSG/CAP_SENDMSG (which cap the same content inside an
# AGENT's own stream): this row is the session's record of what was said, and on the
# web its body sits behind a click. Capped at all because the terminal paints it
# inline, where an unbounded report is a wall; 60 lines is `CAP_BODY`'s ceiling for a
# command's output, and a teammate's review report is the same order of thing.
CAP_TEXT = 60


def sent_ops(frm, to, summ, text, mid, log=None):
    """A message SENT -> the mirror ops for it: one `✉ <frm> → <to>` header plus the
    message as its body, sharing a copy-group so the web has one block whose click
    opens the text. Built HERE, with the rest of the mail vocabulary, and emitted by
    the SendMessage hook (mail_fmt.py) — the only source that sees every message, at
    the moment it is sent, with its full text.

    `mid` is the msg_id from the tool response: the poller's delivery/read rows for
    the same message carry it too, so the web counts one message however many rows
    speak about it."""
    body = SF.cap((text or "").strip(), CAP_TEXT)
    g = O.new_group(log) if (log and body) else None
    ops = [O.label("%s %s → %s" % (GLYPH_SENT, frm, to), MSG_NEW_RGB,
                   g=g, mid=mid, lk=O.COPY_ALL if g else None,
                   act=O.ACT_MAIL, note=note_message(frm, to, summ))]
    if body:
        ops.append(O.gut(body, MSG_NEW_RGB, g=g, mid=mid))
    return ops


def event_ops(events, log=None):
    """[(kind, from, to, summary[, text, msg_id])] transitions -> the mirror ops
    that show them. Returns a list (possibly empty) for the caller to emit into the
    mirror log, so the shape lives with the tracker that produces the events rather
    than with the renderer that paints them. The 5th/6th fields are optional — a
    4-tuple caller still works.

    These are the POLLER's rows and they are PLUMBING: one line per transition, no
    body, labelled `Mail … · delivered` / `· read` / `· idle` (a lifecycle frame's
    own phrase — see `frame`). They deliberately no longer carry the message text:
    the send-time row does (`sent_ops`), and the poller's view of the same message
    would be a second copy of it — worse, a copy that only exists when the mail
    happened to still be unread at a tick. The `text` argument survives to word a
    lifecycle frame, which has no SendMessage row anywhere and so exists ONLY here.

    Every row carries the msg_id as `mid`, so all of a message's rows count as one
    message on the web (docs/dashboard.md, *View modes*)."""
    ops = []
    for ev in events:
        kind, frm, to, summ = ev[:4]
        text = (ev[4] if len(ev) > 4 else "") or ""
        mid = (ev[5] if len(ev) > 5 else "") or ""
        pair = frm + " → " + to
        if kind == "new":
            fr = frame(text)
            state = frame_words(fr)[0] if fr else "delivered"
            ops.append(O.label("%s %s · %s" % (GLYPH_NEW, pair, state),
                               MSG_NEW_RGB, mid=mid, act=O.ACT_MAIL,
                               note=note_mail(frm, to, state)))
        else:                                        # read
            ops.append(O.label(READ_PREFIX + pair, MSG_READ_RGB, mid=mid,
                               act=O.ACT_MAIL, note=note_mail(frm, to, "read")))
    return ops


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
    (recipient, msg_id): {key: read_bool}, {key: (from, recipient, summary, text)}
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
            # `text` is the message itself — the field Claude Code writes the body
            # into (the inbox record is {type, from, text, timestamp, read, color,
            # summary, msg_id}). It is what the mail block shows behind the click;
            # only the SUMMARY is persisted in the tracker state, so a full report
            # is never copied into the state DB.
            meta[k] = (m.get("from") or "?", recipient, m.get("summary") or "",
                       m.get("text") or "")
            ts[k] = _msg_epoch(m.get("timestamp"))
    return states, meta, ts


def update_messages(log):
    """Stateful team-message tracker. Scans inboxes, diffs against the persisted
    state (core.state's messages table + cumulative counters — was a .msgs.json
    sidecar) keyed by msg_id, updates the counters, and returns (parts, events):
      parts  — [(kind, text)] census for the ✉ row: msgs / unread / read; always leads
               with a msgs count (0 included) so the row is never blank, even for a
               non-team session.
      events — [(kind, from, recipient, summary, text, msg_id)] transitions to surface
               in the mirror; kind is 'new' (just delivered — still unread) or 'read'
               (consumed). `text` is the message body (empty for a read — there is
               nothing new to show); `msg_id` names the MESSAGE, so the web counts an
               arrival and its read as one message rather than two.
    Idempotent when nothing changed (a repaint with an unchanged inbox emits no events
    and rewrites nothing), so it is safe to call on every render — incl. resize repaints."""
    d = team_dir(log)
    if not d:
        return [("msgs", "0 msgs")], []      # non-team: still show a 0 count, no events
    delivered0, read0, live = S.msgs_state(log)
    delivered, read = delivered0, read0
    cur, meta, ts = _scan_inbox(d)       # keyed by (recipient, msg_id) — see _scan_inbox
    events = []
    # The msg_id is the key's second half (k = (recipient, msg_id)), so every event
    # can name its message — including a DRAINED one, whose inbox record is already
    # gone and whose only surviving trace is the tracked key.
    for k, is_read in cur.items():       # deliveries — copies present now we hadn't seen
        if k not in live:
            delivered += 1
            frm, to, summ, text = meta[k]
            events.append(("new", frm, to, summ, text, k[1]))
            if is_read:                  # arrived already read (fast consumer)
                read += 1
                events.append(("read", frm, to, summ, "", k[1]))
    for k, ent in list(live.items()):    # reads/drains among copies we were tracking
        was_read = bool(ent[0])
        if k not in cur:                 # drained -> consumed => read
            if not was_read:
                read += 1
                events.append(("read", ent[1], ent[2], ent[3], "", k[1]))
        elif cur[k] and not was_read:    # flipped read:true in place
            read += 1
            frm, to, summ, _text = meta[k]
            events.append(("read", frm, to, summ, "", k[1]))
    # Tracked state keeps the SUMMARY only (the read event it feeds needs no body).
    new_live = {k: [cur[k], meta[k][0], meta[k][1], meta[k][2]] for k in cur}
    if delivered != delivered0 or read != read0 or new_live != live:
        S.msgs_write(log, delivered, read, new_live)
    # Audit message-tracker transitions (only when something actually changed —
    # this runs on every scorebar tick). One row per delivery/read event plus the
    # resulting cumulative counters, so a wrong ✉ census is traceable. The msg_id
    # and the body's LENGTH are recorded (never the body — an audit row is not the
    # place for a 4 KB report), which is what makes "the mail line showed nothing"
    # answerable: an arrival with chars=0 had no text to paint.
    if events:
        A.state_file(log, S.db_path(log), "msg-transitions", {
            "events": [{"kind": k, "from": f_, "to": t, "summary": s,
                        "msg_id": mid, "chars": len(text or "")}
                       for k, f_, t, s, text, mid in events],
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
