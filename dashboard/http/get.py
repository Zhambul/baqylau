# dashboard/http/get.py — GET routing (the read plane) + the copy/view fetchers.
#
# do_GET dispatches to the read-side payload builders (dashboard/read/) and the
# events/* SSE streams; get_copy/get_view serve a mirror block's ⧉ copy text /
# click-to-view stash, both audited like their terminal twins.
import os
from urllib.parse import parse_qs, unquote, urlparse

import plugins
from core import copy as CP
from core import paths as P
from core import sessionapi as API
from core.noaudit import load_audit
from dashboard import config, dictate, prefs, webpush
from dashboard.config import RESUMABLE_MAX
from dashboard.notify import presence
from dashboard.read.lists import (accounts_payload, resumable_payload, sessions_payload,
                                  stats_payload, wire_row)
from dashboard.read.mirror import (history, merged_backlog, note_payload,
                                   ops_payload, view_payload, HISTORY_BLOCKS,
                                   TAIL_BLOCKS)
from dashboard.read.session import session_payload
from dashboard.http.base import _qint, _qstr, valid_sid

A = load_audit()


class _GetMixin:

    # -- routing --
    def do_GET(self):
        url = urlparse(self.path)
        parts = [unquote(p) for p in url.path.strip("/").split("/") if p]
        try:
            self.route(url, parts)
        except (BrokenPipeError, ConnectionResetError):
            pass                            # client disconnect is not an error
        except Exception:
            A.error("", "dashboard request", {"path": self.path[:200]})
            try:
                self._json({"error": "internal"}, 500)
            except Exception:
                pass

    # The read plane as a REGISTRY, the twin of post.py's _FIXED_POST /
    # _SESSION_POST (styleguide: tables over if/elif ladders). _FIXED_GET maps a
    # full fixed path tuple to its handler; _SESSION_GET maps a one-segment
    # session verb (/api/session/<sid>/<verb>) to its handler. Adding an endpoint
    # is a one-line entry plus a named method that documents itself — this was a
    # 165-line ladder in which the routing (three lines) was invisible between
    # the arms' design comments, and where the two shapes of "nothing here" 404
    # sat 100 lines apart.
    #
    # Handler signatures are UNIFORM so the table can call them blind: a fixed
    # handler takes (url), a session handler takes (sid, url). Most ignore `url`
    # — passing it always is what keeps the dispatch a getattr instead of a
    # per-endpoint argument decision.
    _FIXED_GET = {
        ("sessions",): "get_sessions", ("accounts",): "get_accounts",
        ("stats",): "get_stats", ("dictate",): "get_dictate",
        ("commands",): "get_commands", ("ns-prefs",): "get_ns_prefs",
        ("ns-draft",): "get_ns_draft", ("resumable",): "get_resumable",
        ("push", "config"): "get_push_config", ("limits",): "get_limits",
        ("notify-config",): "get_notify_config",
        ("dirs", "hidden"): "get_hidden_dirs",
    }
    _SESSION_GET = {
        "ops": "get_ops", "history": "get_history", "backlog": "get_backlog",
        "errors": "get_errors",
        "monitors": "get_monitors", "jobs": "get_jobs", "memory": "get_memory",
        "note": "get_note",
    }

    def route(self, url, parts):
        if not parts:
            return self.static("index.html")
        if parts[0] == "static" and len(parts) == 2:
            return self.static(parts[1])
        if parts == ["sw.js"]:
            # the push service worker, served at the root so its scope is the
            # whole origin (docs/dashboard.md *Web push*) — not under /static/,
            # which would scope it to /static/.
            return self.static("sw.js")
        if parts == ["favicon.ico"]:
            # the raster fallback favicon, at the root path clients probe on
            # their own when the declared SVG icon is unusable (iOS Safari has
            # no SVG-favicon support at all). Undeclared on purpose — see
            # config.STATIC. docs/dashboard.md *Favicon fallback*.
            return self.static("favicon.ico")
        if parts[0] == "events":
            return self.route_events(url, parts[1:])
        if parts[0] != "api":
            return self._not_found()
        api = parts[1:]
        fixed = self._FIXED_GET.get(tuple(api))
        if fixed:
            return getattr(self, fixed)(url)
        if len(api) >= 2 and api[0] == "session" and valid_sid(api[1]):
            return self.route_session(url, api[1], api[2:])
        return self._not_found()

    def _not_found(self):
        return self._json({"error": "not found"}, 404)

    def route_events(self, url, rest):
        """The SSE streams (/events/…). Not table-matched: each form has its own
        arity AND its own cursor query params, so the shapes are the routing.

        Agent scope has NO stream of its own — it is `?agent=` on the session
        stream, so a scoped page still gets one connection carrying the tab
        colour, scoreboard and cards alongside that agent's mirror."""
        if not rest:
            return self.sse_global()
        if len(rest) == 2 and rest[0] == "session" and valid_sid(rest[1]):
            return self.sse_session(rest[1], _qint(url, "after"),
                                    _qint(url, "mpos"), _qstr(url, "agent"))
        return self._not_found()

    def route_session(self, url, sid, rest):
        """One session's read plane (/api/session/<sid>/…), `sid` already
        validated. The one-segment verbs come from _SESSION_GET; the rest stay
        explicit because their trailing segment is a NAME (a stash gid, a copy
        selector), not a verb a table could key on. An AGENT is not among them:
        agent scope is a `?agent=` filter on the ordinary session reads, not an
        endpoint of its own (docs/dashboard.md *Agent scope*)."""
        if not rest:
            return self._json(session_payload(sid))
        if len(rest) == 1:
            verb = self._SESSION_GET.get(rest[0])
            if verb:
                return getattr(self, verb)(sid, url)
        if len(rest) == 2 and rest[0] == "view":
            return self.get_view(sid, rest[1])
        if len(rest) == 3 and rest[0] == "copy" \
                and rest[2] in ("cmd", "out", "all"):
            return self.get_copy(sid, rest[1], rest[2])
        return self._not_found()

    # -- the fixed read endpoints --
    def get_sessions(self, url):
        return self._json([wire_row(r) for r in sessions_payload()])

    def get_accounts(self, url):
        return self._json(accounts_payload())

    def get_stats(self, url):
        """the GitHub-Insights-style Stats page: cross-session heatmap / pulse /
        punch card / per-project cards, all server-computed + memo-cached
        (stats_payload). Read-only, no audit rows."""
        return self._json(stats_payload())

    def get_dictate(self, url):
        """feature probe: the page renders mic buttons iff a Deepgram key is
        configured (docs/dashboard.md *Web dictation*) — no key means the feature
        is invisible, never a dead button."""
        return self._json({"available": dictate.available()})

    def get_commands(self, url):
        """the "/" menus (composer + new-session prompt): built-ins + the given
        directory's discovered .claude commands/skills. cwd-keyed, not sid-keyed
        — the new-session form completes for a directory that has no session yet;
        a non-directory degrades to built-ins + user-level entries, never an
        error."""
        cwd = (parse_qs(url.query).get("cwd") or [""])[0]
        if not os.path.isdir(cwd):
            cwd = ""
        return self._json(plugins.slash_commands(cwd))

    def get_ns_prefs(self, url):
        """the new-session form's last-used {cwd, model, effort} — moved off
        per-browser localStorage into the durable global prefs store so a launch
        on one device pre-selects on the next (docs/dashboard.md, *New-session
        prefs*). {} when nothing launched yet."""
        return self._json(prefs.get("new-session", {}))

    def get_ns_draft(self, url):
        """the new-session form's UNSENT first prompts (docs/dashboard.md,
        *New-session draft*) — the whole {cwd: {text, seq}} map, one draft per
        directory, restored into the box on every form open so an accidental
        close (Esc / backdrop click) can't throw a half-typed prompt away. The
        map (not one entry) because the page caches it and switches drafts as the
        form's directory changes, with no round-trip in the way; it is bounded by
        prefs.NS_DRAFT_MAX. {} when there are none."""
        return self._json(prefs.ns_drafts())

    def get_resumable(self, url):
        """the new-session resume picker's rows for one directory (fetched on
        open, dir change, and search): recent sessions in `cwd`, each with the
        model/effort/account it ran under; `q` searches the directory's whole
        history (docs/dashboard.md *Resume picker*). `limit` clamped to
        [1, RESUMABLE_MAX]; a blank/unknown dir yields []. Read-only."""
        cwd = _qstr(url, "cwd")
        limit = min(RESUMABLE_MAX, max(1, _qint(url, "limit") or RESUMABLE_MAX))
        return self._json(resumable_payload(cwd, limit, _qstr(url, "q")))

    def get_push_config(self, url):
        """the Web Push feature probe (docs/dashboard.md *Web push*): the page
        offers the notification opt-in + subscribes only when push is possible
        AND has an application-server key. `enabled` false (no crypto backend /
        no key) keeps the feature invisible, never a dead button. The public key
        is not a secret."""
        key = webpush.public_key()
        return self._json({"enabled": bool(webpush.enabled() and key),
                           "key": key})

    def get_limits(self, url):
        """The server-side numbers the PAGE has to know to behave the same way
        (docs/dashboard.md *Served limits*): the upload cap it must reject an
        over-size attachment against, the rename cap it caps its input at, and
        the presence TTL its heartbeat has to beat. Each of these used to be a
        LITERAL in the JS with a "mirrors the server's X" comment — three copies
        of a fact whose owner is config.py / presence.py, drifting the moment one
        side changes (and VIEW_TTL_S is env-overridable, so the drift needs no
        commit at all: lower CLAUDE_DASH_VIEW_TTL_S past the 8s beat and every
        watched session starts firing off-device alerts while you're looking at
        it). Read-only, no audit rows — a probe like /api/dictate.

        Read MODULE-QUALIFIED (`config.X` / `presence.X`, the styleguide's rule
        for a patchable knob), which here is load-bearing rather than stylistic:
        the number we SERVE must be the number the guard ENFORCES, and a
        by-value `from … import UPLOAD_MAX` copy would freeze at import while
        post_upload's own read moved."""
        return self._json({"upload_max": config.UPLOAD_MAX,
                           "rename_max": config.RENAME_MAX,
                           "view_ttl_s": presence.VIEW_TTL_S})

    def get_notify_config(self, url):
        """the GLOBAL alerts master switch (docs/dashboard.md *Global alerts
        toggle*); the list page seeds its ◉/○ button from this on load. Default
        ON — an absent pref reads True. Live changes fan out over the
        `notify-config` SSE event, so this GET is only the initial seed."""
        return self._json({"enabled": prefs.notify_enabled()})

    def get_hidden_dirs(self, url):
        """the {group_key: hidden_at_epoch} map the ✕ built (docs/dashboard.md
        *Hidden directories*); the page seeds S.hidden from this on load — the
        SSE snapshot carries the session ROWS, not this pref, and only the
        browser that clicks ✕ mutates it, so no SSE push is needed."""
        return self._json(prefs.hidden_dirs())

    # -- one session's read endpoints --
    # `?agent=<id>` re-points the mirror/monitors/jobs reads at ONE agent
    # (docs/dashboard.md *Agent scope*). Absent = the session view, which is the
    # LEAD agent's own work: main-agent-only ops, and jobs/monitors it launched
    # itself. The value is never trusted as a path or SQL — it is only ever
    # compared against ids the read model produced.
    def get_ops(self, sid, url):
        last, items = ops_payload(sid, _qint(url, "after"), _qstr(url, "agent"))
        return self._json({"last": last, "items": items})

    def get_history(self, sid, url):
        row = API.session_row(sid)
        key = P.sid_from_log(row["log"]) if row else sid
        # clamp blocks POSITIVE: a negative ?blocks makes _cut_blocks return
        # len(entries), and _snap then indexes entries[len] → IndexError → 500
        # (crafted-request crash).
        oldest, items = history(sid, key, _qint(url, "before"),
                                max(1, _qint(url, "blocks") or HISTORY_BLOCKS),
                                _qstr(url, "agent"))
        return self._json({"oldest": oldest, "items": items})

    def get_backlog(self, sid, url):
        """The GET twin of the SSE fresh-connect backlog: same merged_backlog
        output, but through _send — which GZIPS it (8-9x on this HTML; SSE frames
        are never compressed), so a remote/tunnel page gets its first paint in
        one compressed round-trip. The page hands the returned cursors to the
        SSE, which then only streams increments (the reconnect contract)."""
        row = API.session_row(sid)
        key = P.sid_from_log(row["log"]) if row else sid
        last, mpos, oldest, items = merged_backlog(sid, key, TAIL_BLOCKS,
                                                   _qstr(url, "agent"))
        return self._json({"last": last, "mpos": mpos,
                           "oldest": oldest, "items": items})

    def get_errors(self, sid, url):
        return self._json(API.errors(sid))

    def get_monitors(self, sid, url):
        """Monitors of the session — the LEAD's own by default, one agent's with
        `?agent=`. Every row is attributed (sessionapi.nested_owners), so the
        filter is a scope check, not a guess."""
        agent = _qstr(url, "agent")
        rows = plugins.monitors(sid) or []
        return self._json({"monitors": [m for m in rows
                                        if (m.get("agent_id") or "") == agent]})

    def get_jobs(self, sid, url):
        return self._json({"jobs": API.jobs(sid, _qstr(url, "agent"))})

    def get_memory(self, sid, url):
        return self._json({"memory": API.memory(sid)})

    def get_note(self, sid, url):
        return self._json(note_payload(_qstr(url, "path"), _qstr(url, "stem")))

    def get_copy(self, sid, gid, what):
        """Serve a mirror block's ⧉ copy text — the WEB twin of the terminal's
        ⧉ link (core.copy.main / claude-copy.py). READ-ONLY: a mode=ro ops scan
        via core.copy.collect, no clipboard, no pane feedback (the browser copies
        client-side). Audited as a `web-copy` state_files row (gid/what/chars —
        chars 0 = the group held nothing of that type), because the dashboard
        calls collect() DIRECTLY and so bypasses every audit row main() writes:
        a web copy must be as reconstructible as the terminal `copy` row. A gone
        session DB or a read failure lands an A.error, mirroring main()'s
        `copy (state DB gone …)` / `copy (read ops)` paths. Always 200 with the
        text (empty on any miss — the same silent no-op the terminal shows)."""
        row = API.session_row(sid)
        log = (row.get("log") if row else "") or P.mirror_log(sid)
        sdb = API.state_db_for(sid)
        if not sdb:
            A.error(log, "dashboard copy (state DB gone)",
                    {"gid": gid, "what": what})
            return self._send(200, "", "text/plain; charset=utf-8")
        try:
            text = CP.collect(sdb, gid, what)
        except Exception:
            A.error(log, "dashboard copy (read ops)", {"gid": gid, "what": what})
            return self._send(200, "", "text/plain; charset=utf-8")
        A.state_file(log, sdb, "web-copy",
                     {"gid": gid, "what": what, "chars": len(text)})
        return self._send(200, text, "text/plain; charset=utf-8")

    def get_view(self, sid, gid):
        """Serve a click-to-view stash rendered to HTML — the WEB twin of the
        terminal's ⧉view toggle (which audits a `view` row on every click).
        READ-ONLY (collapse is client-side, so this fires once per EXPAND).
        Audited as a `web-view` state_files row (gid/ok) so the web expand
        isn't a blind spot: `ok:false` = no stash (pre-feature line / failed
        stash write) → 404, the same no-op the terminal shows."""
        row = API.session_row(sid)
        log = (row.get("log") if row else "") or P.mirror_log(sid)
        sdb = API.state_db_for(sid)
        html = view_payload(sid, gid)
        A.state_file(log, sdb or "", "web-view",
                     {"gid": gid, "ok": html is not None})
        if html is None:
            return self._json({"error": "no stash"}, 404)
        return self._send(200, html, "text/html; charset=utf-8")
