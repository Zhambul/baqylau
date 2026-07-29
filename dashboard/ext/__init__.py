# dashboard/ext — the web-dashboard EXTENSION registry (docs/dashboard.md *Web
# extensions*).
#
# An extension is a per-session, usually project-gated dashboard feature — a
# session tab with a badge, its API routes, optional SSE channels and an
# optional top-level page — declared as ONE package under dashboard/ext/<name>/
# and registered in all_ext() below. The mirror-image of plugins/__init__.py:
# an explicit in-repo list (no discovery), a declared SURFACE, and provider()
# as the single door — an undeclared capability is a KeyError, not a silent
# getattr miss.
#
# LAYERING (docs/styleguide.md *Layering*): ext sits between the presentation
# helpers and the read model — `config / opshtml ← ext ← read / control /
# notify ← http`. Core dashboard files import ONLY this registry root; nothing
# outside dashboard/ext/ may import dashboard.ext.<name> (contract-tested in
# tests/test_l1_contracts.py) — that is the "adding an extension edits no core
# file" guarantee. An ext module may reach its own plugin vocabulary module
# directly with a DASHBOARD_PLUGIN_REACHES row (memory's reaches memory.py).
# An extension's HOOK-SIDE producer half is NOT here: it registers in
# plugins/claude_code/fileobs.py (the file-op observer table) and the
# descriptor's PRODUCER names it for the reader only.
#
# Import-safe: no I/O at import; the fan-outs below build their tables on call
# (one tiny loop over a one-or-two entry list — not worth a memo that would
# fight test monkeypatching).
import collections
from urllib.parse import parse_qs


def all_ext():
    """THE registry — every extension, in declaration order (which is also the
    SSE-channel and payload-stamp order). Adding an extension is one line here
    plus its package."""
    from dashboard.ext import memory
    return [memory]


# What an extension MAY declare — name -> minimum arity for callables, None for
# plain values. provider() refuses anything else, so the registry can't grow
# ad-hoc capabilities that nothing validates. UPPER-CASE entries are required
# constants; the rest are optional.
SURFACE = {
    "NAME": None,          # str — tab key = SSE badge event = payload-field stem
                           #       = the JS descriptor's `name`
    "LABEL": None,         # str — the tab strip's label
    "TAB_AFTER": None,     # str — which built-in tab it follows ("jobs");
                           #       errors always stays last
    "BADGE_SCOPED": None,  # bool — does the badge follow AGENT scope (see the
                           #        BADGES table's `scoped` in read/session.py)
    "PRODUCER": None,      # str, documentation only — the plugins-side producer
                           #       module (fileobs row), "" when read-side only
    "scope": 1,            # (cwd) -> bool — the project gate; cwd arrives
                           #       CANONICAL (read/session canonicalizes once).
                           #       Absent = the tab shows for every session.
    "badge": 2,            # (sid, agent) -> int — the tab badge count. The
                           #       framework applies the off-scope -> 0 gate.
    "payload": 2,          # (data, sid) -> None — stamp extra session_payload
                           #       fields (the framework already stamps
                           #       data[NAME+"_scope"] and the badge count)
    "session_get": None,   # {verb: fn(sid, url) -> jsonable} — GET
                           #       /api/session/<sid>/<verb> routes
    "fixed_get": None,     # {path-tuple: fn(url) -> jsonable} — fixed GET
                           #       /api/<...> routes (a top-level page's API)
    "session_post": None,  # {verb: fn(sid, body) -> jsonable} — POST routes;
                           #       the framework applies the control-plane guard
    "fixed_post": None,    # {path-tuple: fn(body) -> jsonable}
    "sse_chans": 0,        # () -> tuple[Chan] — extra SLOW-cadence channels
                           #       (the badge's own channel derives for free)
}

# One pushed per-session SSE channel (the shape http/sse.py's tables are made
# of — it aliases this): `key` names the last-sent slot, `event` the SSE event,
# `value` produces the payload off the per-tick context (attributes: sid, sdb,
# win, cwd, tpath, eff, tab, agent — http/sse.py's _Tick, THE contract an
# extension's producer may rely on), `wrap` the one-key dict field ({"count":
# n}) or None for verbatim.
Chan = collections.namedtuple("Chan", "key event value wrap")

# An extension's badge, as the framework consumes it: read/session.py weaves
# these into its BADGES table (event=name, field=name+"_count") and owns the
# scope gate application — the ONE place a badge meets a scope stays there.
Badge = collections.namedtuple("Badge", "name scoped badge scope")


def provider(ext, name):
    """`ext`'s declared capability `name`, or None when this extension doesn't
    provide it. KeyError on a name not in SURFACE — same contract as
    plugins.provider, so a typo'd capability fails loudly at the call site."""
    if name not in SURFACE:
        raise KeyError("undeclared ext capability: %s" % name)
    return getattr(ext, name, None)


def qstr(url, name):
    """A single query-string value off the `url` a route handler receives —
    provided HERE so an extension never imports the http tier (the layering
    above); the http tier's own _qstr is the same one-liner."""
    return (parse_qs(url.query).get(name) or [""])[0]


def badge_rows():
    """Every extension's badge as a Badge row, declaration order."""
    rows = []
    for e in all_ext():
        b = provider(e, "badge")
        if b:
            rows.append(Badge(e.NAME, bool(e.BADGE_SCOPED), b,
                              provider(e, "scope")))
    return tuple(rows)


def _routes(kind):
    """One merged route table across extensions ({verb-or-tuple: fn}); a verb
    two extensions both claim is a bug, failed loudly at the first request (and
    pinned by the contract test) rather than silently last-wins."""
    table = {}
    for e in all_ext():
        for verb, fn in (provider(e, kind) or {}).items():
            if verb in table:
                raise ValueError("ext route claimed twice: %r" % (verb,))
            table[verb] = fn
    return table


def session_gets():
    return _routes("session_get")


def fixed_gets():
    return _routes("fixed_get")


def session_posts():
    return _routes("session_post")


def fixed_posts():
    return _routes("fixed_post")


def sse_chans():
    """Every extension's extra SSE channels, declaration order (spliced onto
    http/sse.py's _SLOW_CHANS — an extension's live updates ride the slow
    cadence; nothing an extension shows is something a user is blocked on)."""
    chans = []
    for e in all_ext():
        fn = provider(e, "sse_chans")
        if fn:
            chans.extend(fn())
    return tuple(chans)
