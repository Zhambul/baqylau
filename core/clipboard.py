# core/clipboard.py — read the LOCAL machine's pasteboard for copied FILE
# PATHS. The ONE
# owner of the "what files are on the clipboard" fact.
#
# Why this exists at all. Copying a file in an app that offers it as a PROMISE
# (IntelliJ IDEA's Project-view Copy) puts these flavors on the macOS
# pasteboard:
#
#   public.utf8-plain-text   "__init__.py"                    ← the BARE NAME
#   NSFilenamesPboardType    ["/Users/…/clients/__init__.py"] ← the full path
#   public.file-url          "file:///Users/…/__init__.py"    ← the full path
#
# The browser is shown NONE of the path-bearing ones. Chrome hands the page a
# zero-byte `File` whose `.name` is a BASENAME by design — the web platform
# deliberately never exposes a filesystem path to script, and no clipboard API
# (`clipboardData.getData`, `navigator.clipboard.read`) will surface
# `public.file-url`. The paste event's text flavor is the bare name, which is
# not what the user copied and not what the terminal pastes.
#
# So the page cannot answer this question and the SERVER must: the dashboard
# runs on the same Mac as the pasteboard, so it reads the same flavor the
# terminal reads. That is the whole trick — the browser reports WHICH file (basename +
# zero bytes), the server supplies WHERE it is.
#
# Read-only, no caching (a pasteboard is live state — a stale answer is a wrong
# path), and it degrades to [] on every failure: no pyobjc, no pasteboard, a
# non-macOS host. The caller then falls back to the bare name.
#
# Env knob (read at CALL time — the in-process test server flips it per-test):
# BAQYLAU_DASHBOARD_CLIPBOARD_FILES is a `:`-separated path list that REPLACES the
# real pasteboard read, which is what makes this hermetically testable (and
# testable at all off macOS).
import os

from audit import record as A
from urllib.parse import unquote, urlparse

ENV_FILES = "BAQYLAU_DASHBOARD_CLIPBOARD_FILES"
FILES_MAX = 20          # a sane multi-select ceiling; a runaway pasteboard
#                         must not become a runaway message
NAMES_TYPE = "NSFilenamesPboardType"   # plist array of POSIX paths (multi-file)
URL_TYPE = "public.file-url"           # a single file:// URL (the fallback)


def _from_env():
    """The test/override channel: an explicit path list, or None when unset."""
    raw = os.environ.get(ENV_FILES)
    if raw is None:
        return None
    return [p for p in raw.split(":") if p]


def _from_pasteboard():
    """The real read: every file path on the general pasteboard, in order.

    pyobjc is imported HERE, not at module scope — the dashboard imports this
    module on every request path and must not pay (or crash on) an AppKit load
    it may never need. AppKit ships with the system python3 on macOS; anywhere
    else the ImportError is the caller's "no clipboard" answer."""
    from AppKit import NSPasteboard  # noqa: PLC0415 — optional macOS-only dep; ImportError IS the answer
    pb = NSPasteboard.generalPasteboard()
    if pb is None:
        return []
    # NSFilenamesPboardType first: it is the only flavor that carries MORE than
    # one file (a multi-select copy), and it is already POSIX paths.
    plist = pb.propertyListForType_(NAMES_TYPE)
    if plist:
        return [str(p) for p in plist]
    url = pb.stringForType_(URL_TYPE)
    if url:
        u = urlparse(str(url).rstrip("\x00"))
        if u.scheme == "file" and u.path:
            return [unquote(u.path)]
    return []


def files():
    """The absolute paths of the files currently on the local clipboard —
    existing ones only, capped at FILES_MAX. [] when there are none, when the
    host has no readable pasteboard, or on ANY failure (audited, never raised:
    a clipboard read must not 500 a control-plane POST)."""
    try:
        paths = _from_env()
        if paths is None:
            paths = _from_pasteboard()
    except Exception as e:
        A.error("", "clipboard (read failed)",
                {"err": ("%s: %s" % (type(e).__name__, e))[:200]})
        return []
    return [p for p in paths if p and os.path.isabs(p)
            and os.path.exists(p)][:FILES_MAX]


def match(names):
    """The clipboard's paths IFF they are the files the BROWSER just reported
    pasting — same basenames, same count, order-insensitive. Else [].

    This correlation is the whole safety story. The dashboard is reachable from
    a phone over the tunnel, and a phone's clipboard is not this Mac's: without
    the check, any remote paste would be answered with whatever path happens to
    sit on the host's pasteboard — a wrong path silently pasted into a message,
    and a small disclosure of the host's filesystem to a device that never
    copied anything. Requiring the basenames to agree means we only ever
    RESOLVE a file the caller already named; we never volunteer one."""
    want = sorted(n for n in (names or []) if isinstance(n, str) and n)
    if not want:
        return []
    got = files()
    if sorted(os.path.basename(p) for p in got) != want:
        return []
    return got
