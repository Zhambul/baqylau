# dashboard/http/post — POST routing (the control plane).
#
# The write endpoints that TYPE INTO a terminal / launch sessions: message,
# command, stop, interrupt, rename, migrate, rewind, answer, plan, ask/composer
# drafts, uploads, new-session, presence, push, dictation, clientlog. Each is
# guarded (JSON + custom header + origin) so no cross-origin page can fire them.
#
# This module is the ROUTER and nothing else; the handlers live one file per
# concern beside it and compose into `_PostMixin`:
#
#   typing.py     message / command / stop / rewind / rewind-to — the ones that
#                 reach a live TUI with text (or close its tab)
#   interrupt.py  THE stop gesture + its screen-delta probes
#   dialogs.py    the ask + plan cards (drive the TUI's own modal dialogs)
#   state.py      pure state writes: drafts, queue, prefs, mutes, view mode,
#                 hidden dirs, push subscriptions — no terminal touched
#   telemetry.py  the browser's write-only beacons + presence heartbeats
#   files.py      attachment staging, clipboard paths, dictation grants
#   session.py    new-session / migrate / rename
#
# Why mixins rather than free functions: a handler routinely needs a helper that
# belongs to another concern (post_message uses files.py's _attachment_paths;
# every typing handler uses base.py's _post_guard / _reject_input /
# _audit_target), and composing keeps every one of those an ordinary `self.`
# call instead of a threaded-through handler argument. What the split buys is
# that no single file is 2000 lines of twelve unrelated subjects, and that the
# two registries below hold FUNCTION OBJECTS rather than method-name strings —
# a typo is now an ImportError at start-up, not a 500 on the one request that
# happens to hit it.
from urllib.parse import unquote, urlparse

from core.noaudit import load_audit
from dashboard.http.base import valid_sid
from dashboard.http.post.dialogs import _DialogMixin
from dashboard.http.post.files import _FilesMixin
from dashboard.http.post.interrupt import _InterruptMixin
from dashboard.http.post.session import _SessionMixin
from dashboard.http.post.state import _StateMixin
from dashboard.http.post.telemetry import _TelemetryMixin
from dashboard.http.post.typing import _TypingMixin

A = load_audit()


class _PostMixin(_TypingMixin, _InterruptMixin, _DialogMixin, _StateMixin,
                 _TelemetryMixin, _FilesMixin, _SessionMixin):
    """The composed control plane: the routing below plus every handler mixin."""

    # -- POST routing (the control plane) --
    # The dashboard is READ-ONLY except these control-plane writes, which TYPE INTO a
    # terminal — a drive-by RCE if a random website could fire them. Any page
    # can send a *simple* cross-origin POST at 127.0.0.1 (no preflight), so the
    # defense is to make these NON-simple: require a JSON content type AND a
    # custom header (each forces a CORS preflight that a cross-origin page can't
    # pass, since we answer OPTIONS with a bare 501 — no Access-Control-Allow-*),
    # and additionally reject any Origin that isn't our own. See docs/dashboard.md.
    def do_POST(self):
        # No POST route reads the QUERY STRING — the JSON body is the whole
        # payload — so route_post takes only the path parts (unlike GET's
        # route(), which needs `url` for ?after/?cwd/?blocks).
        parts = [unquote(p) for p in urlparse(self.path).path.strip("/").split("/") if p]
        try:
            self.route_post(parts)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            A.error("", "dashboard POST", {"path": self.path[:200]})
            try:
                self._json({"error": "internal"}, 500)
            except Exception:
                pass

    # The POST control plane as a REGISTRY (styleguide: tables over if/elif
    # ladders). _SESSION_POST maps a session-scoped verb (/api/session/<sid>/<v>)
    # to its handler; _FIXED_POST maps a full fixed path tuple to its handler.
    # Adding an endpoint is a one-line entry — the matching (len==3 + valid sid
    # for session verbs, exact tuple for fixed) lives once, in route_post.
    #
    # The values are the FUNCTIONS, not their names. As method-name strings
    # resolved by getattr they forced every handler into one class (which is how
    # this file reached 2000 lines) and turned a typo into a 500 on the one
    # request that hit that row; as function objects the table can span the
    # mixin modules and a typo is an ImportError at start-up.
    _SESSION_POST = {
        "message": _TypingMixin.post_message,
        "command": _TypingMixin.post_command,
        "stop": _TypingMixin.post_stop,
        "interrupt": _InterruptMixin.post_interrupt,
        "rename": _SessionMixin.post_rename,
        "migrate": _SessionMixin.post_migrate,
        "rewind": _TypingMixin.post_rewind,
        "rewind-to": _TypingMixin.post_rewind_to,
        "answer": _DialogMixin.post_answer,
        "ask-draft": _DialogMixin.post_ask_draft,
        "composer-draft": _StateMixin.post_composer_draft,
        "composer-queue": _StateMixin.post_composer_queue,
        "hint-audit": _TelemetryMixin.post_hint_audit,
        "client-fail": _TelemetryMixin.post_client_fail,
        "plan-options": _DialogMixin.post_plan_options,
        "plan-decision": _DialogMixin.post_plan_decision,
        "notify": _StateMixin.post_notify_mute,
        "viewing": _TelemetryMixin.post_viewing,
        "viewmode": _StateMixin.post_view_mode,
        "tasks-hide": _StateMixin.post_tasks_hide,
    }
    _FIXED_POST = {
        ("presence",): _TelemetryMixin.post_presence,
        ("upload",): _FilesMixin.post_upload,
        ("sessions", "new"): _SessionMixin.post_new_session,
        ("ns-prefs",): _StateMixin.post_ns_prefs,
        ("ns-draft",): _StateMixin.post_ns_draft,
        ("dirs", "hide"): _StateMixin.post_hide_dir,
        ("dictate", "token"): _FilesMixin.post_dictate_token,
        ("push", "subscribe"): _StateMixin.post_push_subscribe,
        ("push", "unsubscribe"): _StateMixin.post_push_unsubscribe,
        ("clientlog",): _TelemetryMixin.post_client_log,
        ("clipboard", "files"): _FilesMixin.post_clipboard_files,
        ("notify",): _StateMixin.post_notify_global,
    }

    def route_post(self, parts):
        api = parts[1:] if parts[:1] == ["api"] else None
        if api is None:
            return self._json({"error": "not found"}, 404)
        if len(api) == 3 and api[0] == "session" and valid_sid(api[1]) \
                and api[2] in self._SESSION_POST:
            return self._SESSION_POST[api[2]](self, api[1])
        fixed = self._FIXED_POST.get(tuple(api))
        if fixed:
            return fixed(self)
        return self._json({"error": "not found"}, 404)
