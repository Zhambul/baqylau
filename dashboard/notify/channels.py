# dashboard/notify/channels.py — HOW an off-device alert is delivered, and
# un-delivered (docs/dashboard.md, *Telegram alerts* / *Web push* / *Alert
# retraction*).
#
# The split this module exists for: notifier.py decides WHEN an alert should
# happen (the tab diff, the grace window, the arm/cancel/escalate state
# machine); channels.py knows WHAT that means on the wire. Before retraction
# existed the two were one file, and it read fine — a send was a leaf call. A
# retraction is not a leaf: it is a SECOND wire operation that has to reach the
# exact thing the first one produced, so "what was delivered, and what does it
# take back" became a fact needing an owner. That fact is the HANDLE below.
#
# Every send returns a handle or None. None means nothing retractable was
# delivered — either the channel was off/unconfigured, or nobody was subscribed.
# A handle is an opaque dict to the caller: it carries `ch`, and the notifier's
# only business with it is storing it and later passing it back to `retract()`.
#
# Both directions are BEST-EFFORT and audited here rather than at the call site,
# because the audit row's shape is per-channel (a Telegram message id vs a push
# endpoint + device) and the watcher shouldn't have to know either. Nothing in
# this module raises into the 1 s watcher loop.
import threading
from urllib.parse import quote

from core import audit as A
from dashboard import config, prefs, telegram, webpush


# `retract()` outcome vocabulary. Everything except PENDING is settled — the
# caller forgets the record. PENDING means the SEND is still in flight (the
# Telegram round-trip runs on its own thread, so a retraction can genuinely
# arrive first): keep the record and ask again on the next tick.
PENDING = "pending"     # send not landed yet — retry next tick
OK = "ok"               # retracted
GONE = "gone"           # already gone from the chat — the same thing, cheaper
FAILED = "failed"       # the wire said no; the alert is still out there
NOTHING = "nothing"     # the send never landed anything — nothing to take back


def alert_text(entry):
    """The alert pieces both channels build the same way from one `entry`: the
    🔴/🟢 headline (project + needs-you/is-done), the detail line (the session
    title, or a kind-specific fallback), and the ?s=<session_id> deep link. Returns the
    three RAW strings only — each channel composes them differently (Telegram
    joins them into one message; Web Push splits them across the payload's
    title/body), so the joining/escaping stays at the call site.

    ?s=<session_id>, NOT the app's #/s/<session_id> hash route: Telegram's auto-linker drops
    the URL fragment, so a #-link opens the dashboard ROOT on the phone, not the
    session. The session_id rides a query param (linkified whole); the page translates
    ?s=<session_id> back into the hash route on load."""
    asking = entry.get("kind") == "asking"
    proj = entry.get("project") or entry.get("session_id") or "session"
    head = ("🔴 %s needs you" if asking else "🟢 %s is done") % proj
    detail = entry.get("title") or (
        "a question is waiting" if asking else "finished — your turn")
    url = "%s/?s=%s" % (config.PUBLIC_URL, quote(entry.get("session_id") or ""))
    return head, detail, url


def push_tag(session_id):
    """The notification tag a pushed alert is shown under — the ONE encoding of
    it, shared by the sender, the retraction and the service worker (sw.js
    builds the same string). It is what makes a repeat alert REPLACE its
    predecessor instead of stacking, and what the resolve push closes."""
    return "baqylau-%s" % (session_id or "")


# ----------------------------------------------------------------- Telegram

def send_telegram(entry, reason=None):
    """Send the deferred alert to Telegram. `reason` (in the audit row) says WHY
    it fired: `escalation` (the nudge after an on-device push you ignored),
    `no-device` (nobody was push-subscribed — the immediate fallback), or
    `always` (_ALWAYS forced both) — so a Telegram alert is never an unexplained
    duplicate.

    The Bot API call runs on a daemon thread and its `message_id` lands in the
    returned retractable handle. An unconfigured channel returns None."""
    head, title, url = alert_text(entry)
    msg = "%s — %s\n%s" % (head, title, url)
    if not telegram.enabled():
        return None
    # The handle is created NOW and filled by the sender thread, because the
    # watcher must not block on a round-trip and a retraction can beat the send
    # home. `msg_id` None + `done` False is exactly the PENDING state retract()
    # reads. Single assignments of small immutables, read by the one watcher
    # thread — the same "atomic enough" bargain presence.py's maps make.
    h = {"ch": "telegram", "session_id": entry.get("session_id"), "kind": entry.get("kind"),
         "chat": None, "msg_id": None, "done": False}
    threading.Thread(target=_telegram_send_body, args=(h, msg, reason),
                     daemon=True).start()
    return h


def _telegram_send_body(h, msg, reason):
    """The off-watcher send body: call the Bot API, record the id in the handle,
    audit. `done` is set LAST and unconditionally — it is what releases retract()
    from PENDING, so an exception path that skipped it would pin the record until
    its TTL."""
    try:
        res = telegram.send(msg)
    except Exception:
        A.error("", "dashboard telegram notify", {"session_id": h.get("session_id")})
        h["done"] = True
        return
    if res.ok:
        h["chat"], h["msg_id"] = res.chat, res.message_id
    A.state_file("", "", "telegram-notify",
                 {"session_id": h.get("session_id"), "kind": h.get("kind"), "reason": reason,
                  "ok": res.ok, "status": res.status, "error": res.error,
                  # the retraction contract, recorded at the send: an alert with
                  # retractable=False can never be taken back, and this row is
                  # the only place that says so.
                  "retractable": bool(res.ok and res.message_id),
                  "message_id": res.message_id})
    h["done"] = True


def _retract_telegram(h, reason, badge=0):
    """Delete the message — OFF the watcher thread, for the same reason the send
    is: `telegram.delete` is a synchronous HTTPS round-trip with a 10 s timeout,
    and the 1 s scan loop cannot wear that. So the outcome is not known
    synchronously either, and this settles over two ticks: the first spawns the
    delete and answers PENDING, a later one reads what the thread left. The
    caller already retries PENDING (it must, for the in-flight send), so this
    needs no new machinery — and the `notify-retract` row it eventually writes
    still reports what actually happened on the wire, rather than an optimistic
    guess made before the call returned."""
    if not h.get("done"):
        return PENDING                     # the SEND hasn't landed yet
    if h.get("outcome"):
        return h["outcome"]                # the delete thread finished
    if not (h.get("chat") and h.get("msg_id")):
        return NOTHING                     # the send failed — nothing is out there
    if not h.get("deleting"):              # spawn once, however often we're asked
        h["deleting"] = True
        threading.Thread(target=_telegram_delete_body, args=(h,),
                         daemon=True).start()
    return PENDING


def _telegram_delete_body(h):
    """The off-watcher delete body: `outcome` is set on every path (it is what
    releases the retraction from PENDING), and a `gone` message counts as done —
    someone clearing the chat first is the outcome we wanted, not a failure."""
    try:
        res = telegram.delete(h["chat"], h["msg_id"])
    except Exception:
        A.error("", "dashboard telegram retract", {"session_id": h.get("session_id")})
        h["outcome"] = FAILED
        return
    h["outcome"] = OK if res.ok else (GONE if res.gone else FAILED)


# ----------------------------------------------------------------- Web Push

def send_webpush(entry, subs, badge=0):
    """Send the on-device alert as a Web Push to `subs` — the subscriptions of
    the ONE device the caller routed to (`presence.route`), NOT every
    subscription, so a session going done/asking buzzes the device you're
    working on, not your iPad and Mac at once (docs/dashboard.md, *Web push* /
    *Presence routing*). Dispatched on a detached daemon thread: the crypto +
    network round-trips must never stall the 1 s watcher. Best-effort + audited;
    a subscription the push service reports GONE (404/410) is pruned. No-op when
    the crypto backend is missing or `subs` is empty.

    The ROUTING is deliberately not decided here — a transport that picked its
    own destination could not be reused by the retraction, which must reach the
    devices the alert ACTUALLY went to rather than whichever is most-recently-
    used by then. The caller passes the targets and audits `notify-route`.

    Returns a handle (the alert is out on these subscriptions, and a resolve
    push can close it) or None — which the caller reads as "no device to push
    to", the signal that holds Telegram back to the escalation nudge."""
    if not (webpush.enabled() and subs):
        return None
    session_id = entry.get("session_id") or ""
    title, body, url = alert_text(entry)
    payload = {"title": title, "body": body, "session_id": session_id,
               "kind": entry.get("kind"), "url": url, "badge": badge}
    threading.Thread(target=_webpush_fanout, args=(subs, payload, "send"),
                     daemon=True).start()
    # The subscriptions are the handle: a resolve push has to reach the devices
    # the alert actually went to, NOT whichever device is most-recently-used by
    # then — the banner is on the former.
    return {"ch": "webpush", "session_id": session_id, "kind": entry.get("kind"),
            "subs": subs, "tag": push_tag(session_id)}


def _webpush_fanout(subs, payload, action):
    """The detached fan-out body, shared by the alert and its retraction:
    deliver `payload` to each subscription, audit the outcome (with the target
    `device` — the on-device analog of the route decision), and prune the dead
    ones. Runs off the watcher thread; never raises."""
    for sub in subs:
        try:
            res = webpush.send(sub, payload)
        except Exception:
            A.error("", "dashboard webpush %s" % action,
                    {"session_id": payload.get("session_id")})
            continue
        ep = sub.get("endpoint", "") if isinstance(sub, dict) else ""
        dev = sub.get("device") if isinstance(sub, dict) else None
        if res.gone:
            prefs.remove_push_subscription(ep)
        A.state_file("", "", "web-push",
                     {"session_id": payload.get("session_id"), "kind": payload.get("kind"),
                      "action": action, "status": res.status,
                      "ok": res.ok, "gone": res.gone,
                      "badge": payload.get("badge"),
                      "device": dev, "endpoint": ep[:80]})


def _retract_webpush(h, reason, badge=0):
    """Close the delivered banner by pushing a RESOLVE message to the same
    subscriptions; sw.js closes everything under the tag and shows nothing.

    That "shows nothing" is the load-bearing risk of this whole path: an iOS
    subscription is `userVisibleOnly`, and WebKit may answer a push that raises
    no notification with a generic placeholder banner — or, if it keeps
    happening, revoke the subscription. What keeps that survivable is the
    BUDGET: exactly one resolve per delivered alert (the notifier forgets the
    record either way), so the silent:visible ratio is bounded at 1:1 rather
    than being a background chatter channel. BAQYLAU_DASHBOARD_RESOLVE_PUSH=0 turns it
    off, and the page's own foreground sweep (app.01-attention.js) still clears
    stale banners on open — so a refused or dropped resolve degrades to "cleared
    a bit later", never to a wrong badge."""
    if not config.RESOLVE_PUSH:
        return NOTHING
    subs = h.get("subs") or []
    if not subs:
        return NOTHING
    payload = {"type": "resolve", "session_id": h.get("session_id") or "",
               "kind": h.get("kind"), "tag": h.get("tag"), "badge": badge}
    threading.Thread(target=_webpush_fanout, args=(subs, payload, "resolve"),
                     daemon=True).start()
    return OK                              # dispatched; the thread audits the wire


# --------------------------------------------------------------- dispatch

# Registry, not an if/elif ladder (docs/styleguide.md): a new channel adds a
# send function and one row here, and nothing in notifier.py changes.
_RETRACT = {"telegram": _retract_telegram, "webpush": _retract_webpush}


def retract(handle, reason, badge=0):
    """Take back one delivered alert. Returns an outcome from the vocabulary
    above; PENDING is the only one the caller must retry.

    Deliberately does NOT write the `notify-retract` row: the notifier owns that
    action so the lifecycle has ONE writer and one row shape (it also files the
    expiries, which never reach a channel at all). What each channel audits is
    its own WIRE detail — the resolve push's per-device delivery — which the
    notifier could not describe."""
    fn = _RETRACT.get((handle or {}).get("ch"))
    if fn is None:
        return NOTHING
    try:
        return fn(handle, reason, badge)
    except Exception:
        A.error("", "dashboard notify retract", {"session_id": (handle or {}).get("session_id")})
        return FAILED
