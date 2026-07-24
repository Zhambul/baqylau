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
from dashboard import dictate, prefs, webpush
from dashboard.config import (RESUMABLE_MAX)
from dashboard.read.lists import (accounts_payload, resumable_payload, sessions_payload,
                                  stats_payload, _wire_row)
from dashboard.read.mirror import (history, merged_backlog, note_payload,
                                   ops_payload, view_payload, HISTORY_BLOCKS,
                                   _mdify)
from dashboard.read.session import (session_payload, _stamp_agent_cost)
from dashboard.http.base import _qint, _qstr, _sid

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
        if parts[0] == "events":
            if len(parts) == 1:
                return self.sse_global()
            if len(parts) == 3 and parts[1] == "session" and _sid(parts[2]):
                return self.sse_session(parts[2], _qint(url, "after"),
                                        _qint(url, "mpos"))
            if len(parts) == 4 and parts[1] == "agent" \
                    and _sid(parts[2]) and _sid(parts[3]):
                return self.sse_agent(parts[2], parts[3], _qint(url, "pos"))
            return self._json({"error": "not found"}, 404)
        if parts[0] != "api":
            return self._json({"error": "not found"}, 404)
        api = parts[1:]
        if api == ["sessions"]:
            return self._json([_wire_row(r) for r in sessions_payload()])
        if api == ["accounts"]:
            return self._json(accounts_payload())
        if api == ["stats"]:
            # the GitHub-Insights-style Stats page: cross-session heatmap /
            # pulse / punch card / per-project cards, all server-computed +
            # memo-cached (stats_payload). Read-only, no audit rows.
            return self._json(stats_payload())
        if api == ["dictate"]:
            # feature probe: the page renders mic buttons iff a Deepgram key
            # is configured (docs/dashboard.md *Web dictation*) — no key
            # means the feature is invisible, never a dead button
            return self._json({"available": dictate.available()})
        if api == ["commands"]:
            # the "/" menus (composer + new-session prompt): built-ins + the
            # given directory's discovered .claude commands/skills. cwd-keyed,
            # not sid-keyed — the new-session form completes for a directory
            # that has no session yet; a non-directory degrades to built-ins
            # + user-level entries, never an error.
            cwd = (parse_qs(url.query).get("cwd") or [""])[0]
            if not os.path.isdir(cwd):
                cwd = ""
            return self._json(plugins.slash_commands(cwd))
        if api == ["ns-prefs"]:
            # the new-session form's last-used {cwd, model, effort} — moved off
            # per-browser localStorage into the durable global prefs store so a
            # launch on one device pre-selects on the next (docs/dashboard.md,
            # *New-session prefs*). {} when nothing launched yet.
            return self._json(prefs.get("new-session", {}))
        if api == ["resumable"]:
            # the new-session resume picker's rows for one directory (fetched on
            # open, dir change, and search): recent sessions in `cwd`, each with
            # the model/effort/account it ran under; `q` searches the directory's
            # whole history (docs/dashboard.md *Resume picker*). `limit` clamped to
            # [1, RESUMABLE_MAX]; a blank/unknown dir yields []. Read-only.
            cwd = _qstr(url, "cwd")
            limit = min(RESUMABLE_MAX, max(1, _qint(url, "limit") or RESUMABLE_MAX))
            return self._json(resumable_payload(cwd, limit, _qstr(url, "q")))
        if api == ["push", "config"]:
            # the Web Push feature probe (docs/dashboard.md *Web push*): the page
            # offers the notification opt-in + subscribes only when push is
            # possible AND has an application-server key. `enabled` false (no
            # crypto backend / no key) keeps the feature invisible, never a dead
            # button. The public key is not a secret.
            key = webpush.public_key()
            return self._json({"enabled": bool(webpush.enabled() and key),
                               "key": key})
        if api == ["dirs", "hidden"]:
            # the {group_key: hidden_at_epoch} map the ✕ built (docs/dashboard.md
            # *Hidden directories*); the page seeds S.hidden from this on load —
            # the SSE snapshot carries the session ROWS, not this pref, and only
            # the browser that clicks ✕ mutates it, so no SSE push is needed.
            return self._json(prefs.hidden_dirs())
        if len(api) >= 2 and api[0] == "session" and _sid(api[1]):
            sid, rest = api[1], api[2:]
            if not rest:
                return self._json(session_payload(sid))
            if rest == ["ops"]:
                last, items = ops_payload(sid, _qint(url, "after"))
                return self._json({"last": last, "items": items})
            if rest == ["history"]:
                row = API.session_row(sid)
                key = P.sid_from_log(row["log"]) if row else sid
                # clamp blocks POSITIVE: a negative ?blocks makes _cut_blocks
                # return len(entries), and _snap then indexes entries[len] →
                # IndexError → 500 (crafted-request crash).
                oldest, items = history(sid, key, _qint(url, "before"),
                                        max(1, _qint(url, "blocks")
                                            or HISTORY_BLOCKS))
                return self._json({"oldest": oldest, "items": items})
            if rest == ["backlog"]:
                # The GET twin of the SSE fresh-connect backlog: same
                # merged_backlog output, but through _send — which GZIPS it
                # (8-9x on this HTML; SSE frames are never compressed), so a
                # remote/tunnel page gets its first paint in one compressed
                # round-trip. The page hands the returned cursors to the SSE,
                # which then only streams increments (the reconnect contract).
                row = API.session_row(sid)
                key = P.sid_from_log(row["log"]) if row else sid
                last, mpos, oldest, items = merged_backlog(sid, key)
                return self._json({"last": last, "mpos": mpos,
                                   "oldest": oldest, "items": items})
            if rest == ["activity"]:
                return self._json(_mdify(plugins.activity(sid)) or {"entries": []})
            if len(rest) == 2 and rest[0] == "agent":
                tl = _mdify(plugins.activity(sid, rest[1]))
                if tl is not None:
                    _stamp_agent_cost(tl)
                return self._json(tl if tl is not None else {"entries": []})
            if rest == ["errors"]:
                return self._json(API.errors(sid))
            if rest == ["monitors"]:
                return self._json({"monitors": plugins.monitors(sid) or []})
            if rest == ["jobs"]:
                return self._json({"jobs": API.jobs(sid)})
            if rest == ["memory"]:
                return self._json({"memory": API.memory(sid)})
            if rest == ["note"]:
                return self._json(note_payload(_qstr(url, "path"),
                                               _qstr(url, "stem")))
            if len(rest) == 2 and rest[0] == "view":
                return self.get_view(sid, rest[1])
            if len(rest) == 3 and rest[0] == "copy" \
                    and rest[2] in ("cmd", "out", "all"):
                return self.get_copy(sid, rest[1], rest[2])
        return self._json({"error": "not found"}, 404)

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
