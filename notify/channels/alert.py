# notify/channels/alert.py — what an alert SAYS, and how a send can end.
#
# The two pieces both channels share. `alert_text` builds the same three
# strings from one entry (each channel composes them differently); `push_tag`
# is the one encoding of the notification tag, which the sender, the retraction
# and the service worker (static/sw.js) must all agree on.
#
# The outcome vocabulary lives here rather than in the package __init__ so a
# channel can name it without importing the dispatcher that imports the
# channel.
from urllib.parse import quote

from dashboard import config


# `retract()` outcome vocabulary. Everything except PENDING is settled — the
# caller forgets the record. PENDING means the SEND is still in flight (the
# Telegram round-trip runs on its own thread, so a retraction can genuinely
# arrive first): keep the record and ask again on the next tick.
PENDING = "pending"     # send not landed yet — retry next tick
OK = "ok"               # retracted
GONE = "gone"           # already gone from the chat — the same thing, cheaper
FAILED = "failed"       # the service said no; the alert is still out there
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
