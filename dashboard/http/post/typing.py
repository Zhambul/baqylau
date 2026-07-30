# dashboard/http/post/typing.py — the control-plane POSTs that TYPE INTO a
# session's kitty window: message, quick command, stop (close the tab), and the
# two rewind gestures. Every one of them resolves the AUTHORITATIVE live window
# first (_resolve_live_window), then hands the gesture to the session's OWNING
# HOST (_gesture_host → plugins/<tool>/hostctl.py), which drives the terminal and
# writes the `web-*` state_files row. What stays here is the guarding: input
# validation, the caps gate, the live window, the tab-state refusals, and the
# HTTP mapping of the gesture's result.
import plugins
from core import sessionapi as API
from core import tabs
from core.noaudit import load_audit
from dashboard.config import (BUSY_TABS,
                              EFFORTS,
                              QUEUE_TABS,
                              MODEL_ARG_OK)
from dashboard.control import launch
from dashboard.read.session import (ask_pending, plan_pending)

A = load_audit()

# The quick-command → host CAPABILITY key each command is gated on
# (_caps_guard). `rename` is the argless AUTO-rename (the ✦ button), and it
# rides the `rename` cap rather than one of its own — being told a name and
# inventing one are the same capability from the button's point of view. It used
# to be absent, which is how Claude Code's argless `/rename` got pasted into a
# codex composer that has no such command (the P2 bug list, item 3); the host's
# `autoname` gesture is the other half of that fix, since a host may implement
# `rename` and not `autoname` (codex does) and only the gesture can say so.
CAP_BY_CMD = {"compact": "compact", "model": "model", "effort": "effort",
              "rename": "rename"}


class _TypingMixin:
    """The handlers that reach a live TUI with text or a tab close.

    A mixin, composed into Handler alongside the other post/ mixins
    (dashboard/http/post/__init__.py) — so a cross-concern helper stays an
    ordinary `self.` call (post_message uses _FilesMixin's _attachment_paths;
    _escape_press uses this module's _dialog_open_guard) while each concern
    keeps its own file."""

    def post_message(self, sid):
        """Type a message into a session's kitty window (the composer). 4xx when
        the session has no window (headless/daemon) or the text is empty; 503
        when no terminal resolves; else Frontend.send_text. Every attempt is a
        `web-send` state_files row, failures also an A.error.

        The DELIVERY itself is the owning host's `send` gesture (the paste, the
        input clear, the clipboard-image guard, the draft-stash consume — all
        host-specific, all in plugins/<tool>/hostctl.py). This handler owns the
        guards around it: the text/attachment validation, the modal refusal, the
        live window, and the `queued` promise.

        `clear_draft` (bool): the TUI input already holds text the web put
        there — an interrupt that took the last message back, or a rewind that
        restored one — so the send first kills that draft and then delivers the
        text as a bracketed paste. This is what lets you edit AND resend from the
        web without touching the kitty tab (docs/dashboard.md).

        The SERVER decides it (`launch.tui_draft`), OR-ed with the body flag.
        It used to be the page's call alone, remembered in a per-view variable
        — which a RELOAD wiped while the TUI's draft survived, so the next send
        pasted after the leftover and delivered `testingtesting2` (reported
        2026-07-25). A successful send consumes the flag."""
        body = self._post_guard()
        if body is None:
            return
        text = body.get("text")
        if text is None:
            text = ""
        if not isinstance(text, str):
            return self._reject_input("web-send", "bad text", "empty text",
                                      {"type": type(text).__name__}, sid=sid)
        # attachments (vetted @-paths) may stand in for text — a screenshot with
        # no words is a valid message; an empty message with neither is not.
        attachments = self._attachment_paths(body)
        if not text.strip() and not attachments:
            return self._reject_input("web-send", "empty text", "empty text",
                                      {"chars": len(text)}, sid=sid)
        # the owning host supplies the MENTION grammar the attachments ride in
        # (claude_code's `@path`; a host with none gets the bare paths)
        host = self._gesture_host(sid)
        text = self._with_attachments(text, attachments, host)
        # the box holds text WE left there (take-back / rewind restore) — the
        # server's own record, so a reload or another device can't lose it
        pending_draft = launch.tui_draft(sid)
        clear_draft = bool(body.get("clear_draft")) or bool(pending_draft)
        # AUTHORITATIVE window (see _resolve_live_window): the pane tagged
        # claude_session=<sid>, NOT the audit row's stale start-time id (typing
        # into a reused id would land in an unrelated tab — a fresh scan, never
        # the TTL memo). 503/409 each a `web-send` failure row.
        resolved = self._resolve_live_window(
            sid, "web-send", verb="message", extra={"chars": len(text)})
        if resolved is None:
            return
        row, log, sdb, fe, win, tab = resolved
        # a message pasted while a MODAL dialog (AskUserQuestion / ExitPlanMode)
        # is up goes INTO the dialog, not the TUI message queue — it perturbs
        # the dialog and the text is lost (the "my queued message vanished mid
        # ask" report, 2026-07-19). Refuse with a clear pointer to the card; the
        # composer keeps its text (the page re-persists the draft on error).
        if ask_pending(sid) or plan_pending(sid):
            A.state_file(log, sdb, "web-send",
                         {"win": win, "chars": len(text), "ok": False,
                          "blocked": "modal"})
            return self._json({"error": "this session has an open question — "
                               "answer it in the card above (or dismiss it) "
                               "before sending", "modal": True}, 409)
        # the tab state AT SEND TIME (resolved above) decides whether this send
        # starts a turn or lands in the TUI's message queue (QUEUE_TABS); it
        # rides the audit row too — "my message vanished" is answerable as "it
        # queued mid-turn".
        #
        # But the colour alone cannot PROMISE `queued`, and the page acts on that
        # promise by pinning a ⧗ chip. Claude Code fires no hook on cancel, so a
        # turn cancelled AT THE TERMINAL (Esc-Esc) leaves the tab frozen on
        # magenta: the send then promises `queued` for a message the idle TUI
        # submits instantly, and the chip has no delivery to wait for (session
        # bdeca061, 2026-07-25 — UserPromptSubmit fired 0.1s after a
        # `tab: thinking` send). So VERIFY the turn is really live first. The
        # probe must run BEFORE the paste: our own paste changes the screen and
        # would itself read as motion.
        live = host.turn_live(fe, win, {"sid": sid}) if tab in QUEUE_TABS \
            else None
        queued = tab in QUEUE_TABS and live is not False
        res = host.send(fe, win, text, {
            "sid": sid, "log": log, "sdb": sdb, "tab": tab,
            "action": "web-send", "verb": "message",
            "clear_draft": clear_draft, "prev_text": pending_draft,
            "box": launch.WebBox(sid), "live": live, "queued": queued,
            "attachments": len(attachments)})
        if self._gesture_declined(res, sid, "web-send", "send",
                                  extra={"win": win, "chars": len(text)}):
            return
        if not res.get("ok"):
            return self._json({"error": "send failed"}, 502)
        return self._json({"ok": True, "queued": queued, "tab": tab})

    def post_command(self, sid):
        """The scoreboard's quick-command row — type one of the TUI's OWN
        slash commands into the session's window: `{"cmd": "compact"}` →
        `/compact`, `{"cmd": "model", "arg": <alias|id>}` → `/model <arg>`,
        `{"cmd": "effort", "arg": <level>}` → `/effort <arg>` (both may open
        the TUI's switch-confirm menu, auto-answered Yes below — the reply's
        `confirm` field), `{"cmd": "rename"}` → the host's AUTONAME (bare
        `/rename` makes Claude Code GENERATE the title itself; a NAMED rename
        never comes through here, post_rename's transcript append works parked
        too). A FIXED vocabulary, 400 on anything else — the arg is validated
        (MODEL_ARG_OK / EFFORTS) precisely because it is typed into a terminal,
        and compact/rename take no arg (the closed vocabulary IS the point;
        free-form text is the composer's job). Delivery is the host's own
        gesture, so mid-turn the command lands in the TUI's message queue and
        runs at the turn boundary (`queued` in the reply) — but a RED tab
        (awaiting-command: a modal dialog is up) is a 409: pasted text would
        land IN the dialog, its digits deciding it. Every attempt is a
        `web-command` state_files row, failures also an A.error."""
        body = self._post_guard()
        if body is None:
            return
        cmd, arg = body.get("cmd"), body.get("arg")
        # A NON-default host (codex) drives model/effort through its OWN gesture
        # (an interactive /model picker, not a `/model <arg>` paste), so its arg
        # is validated by the live picker, not Claude's MODEL_ARG_OK/EFFORTS.
        host = self._gesture_host(sid)
        # STRICT for the default host (and for an unnamed/inert one — a registry
        # that could not resolve a host must not thereby relax an allowlist that
        # guards text typed into a terminal); a proven non-default host validates
        # its own arg against its live picker.
        default = host.name in ("", plugins.default_host())
        argful = isinstance(arg, str) and bool(arg)
        if cmd in ("compact", "rename") and not arg:
            pass
        elif cmd == "model" and argful and (not default
                                            or MODEL_ARG_OK.match(arg)):
            pass
        elif cmd == "effort" and ((not default and argful)
                                  or arg in EFFORTS):
            pass
        else:
            return self._reject_input("web-command", "bad cmd", "unknown command",
                                {"sid": sid, "cmd": cmd, "arg": arg})
        # refuse when the owning host can't run this command (no-op for
        # claude_code — compact/model/effort caps are all True; byte-identical)
        cap = CAP_BY_CMD.get(cmd)
        if cap and self._caps_guard(sid, cap, "web-command"):
            return
        # AUTHORITATIVE window (see _resolve_live_window): the live
        # claude_session=<sid> pane tag, same as post_message (a reused stale id
        # would type into an unrelated tab). 503/409 each a `web-command`
        # failure row.
        resolved = self._resolve_live_window(
            sid, "web-command", verb="command",
            extra={"cmd": cmd, "arg": arg or ""})
        if resolved is None:
            return
        row, log, sdb, fe, win, tab = resolved
        if tab == tabs.AWAITING_COMMAND:
            A.state_file(log, sdb, "web-command",
                         {"win": win, "cmd": cmd, "arg": arg or "",
                          "ok": False, "tab": tab})
            return self._json({"error": "a dialog is open — answer it first"},
                              409)
        # Every command is the owning host's own gesture: `compact` its
        # summarise command, `model`/`effort` a `/model <arg>` paste + the
        # switch-confirm auto-Yes for Claude Code and an interactive 3-step
        # picker for codex, `rename` the argless AUTO-name. The handler no longer
        # knows any of those spellings — including whether the host HAS the
        # gesture: `autoname` is the one CAP SHARER here (it rides `rename`), so
        # a host with a `/rename <name>` but no argless form declines and that
        # decline is the 409.
        ctx = {"sid": sid, "log": log, "sdb": sdb, "tab": tab,
               "action": "web-command", "verb": "command",
               "queueing": tab in QUEUE_TABS}
        if cmd == "compact":
            res = host.compact(fe, win, ctx)
        elif cmd == "rename":
            res = host.autoname(fe, win, ctx)
        elif cmd == "model":
            res = host.model(fe, win, arg, ctx)
        else:
            res = host.effort(fe, win, arg, ctx)
        if self._gesture_declined(res, sid, "web-command",
                                  CAP_BY_CMD.get(cmd) or cmd,
                                  extra={"win": win, "cmd": cmd,
                                         "arg": arg or ""}):
            return
        if not res.get("ok"):
            return self._json({"error": res.get("detail") or "send failed",
                               "step": res.get("step") or ""},
                              502)
        out = {"ok": True, "queued": tab in QUEUE_TABS, "tab": tab}
        if res.get("confirm"):
            out["confirm"] = res["confirm"]
        return self._json(out)

    def post_stop(self, sid):
        """Close a session's kitty tab (Frontend.close_tab — main window +
        mirror + scorebar at once). This is a GRACEFUL stop, not a kill: kitty
        HUPs the tab's processes, Claude Code exits and fires SessionEnd, and
        the normal end-of-session lifecycle (mirror park, audit close) runs on
        its own — verified empirically 2026-07-18 (docs/dashboard.md). 409
        when the session has no window (headless — nothing to close); 503
        when no terminal resolves.

        Every attempt is a `web-stop` state_files row carrying a `phase`:
        `attempt` is written BEFORE the (potentially blocking) close_tab, `done`
        after with the `ok` outcome — a lone `attempt` with no paired `done` is
        the stuck-close signal (close_tab hung on an unbounded kitten socket
        connect, or the thread never returned), which the client only shows as a
        `web-hint op=close … stale` beacon. The early no-terminal / no-window
        rejections are terminal `done` rows. Failures also an A.error."""
        body = self._post_guard()
        if body is None:
            return
        # AUTHORITATIVE window (see _resolve_live_window): the pane tagged
        # claude_session=<sid>, NOT the audit row's stale start-time id. Closing
        # by a reused stale id would close an UNRELATED live tab (a leaked
        # smoke-test session's reused window id once closed the user's own tab).
        # 503/409 each a terminal `web-stop` phase=done failure row.
        resolved = self._resolve_live_window(
            sid, "web-stop", verb="stop", extra={"phase": "done"})
        if resolved is None:
            return
        row, log, sdb, fe, win, tab = resolved
        # Audit the ATTEMPT before close_tab: a kitten close that HANGS (an
        # unbounded socket connect) or otherwise never returns must not vanish
        # from the audit — the `web-stop attempt` is the only trace the server
        # ever tried. Same "row before the risky op" discipline as
        # stream_start/stream_end (an attempt with no `done` == the anomaly).
        A.state_file(log, sdb, "web-stop", {"win": win, "phase": "attempt"})
        ok = bool(fe.close_tab(win))
        A.state_file(log, sdb, "web-stop", {"win": win, "phase": "done", "ok": ok})
        # the one lifecycle event no host's OWN hooks describe: "the web closed
        # your tab". Both hosts answer "nothing to do" (Claude Code fires
        # SessionEnd, codex's watcher notices its host pid is gone — each routes
        # into hostpane.host_end by itself), and each says so by overriding it;
        # the call is here so a host that DOES need to park something has the
        # seam, and so the declaration is not dead code.
        self._gesture_host(sid).lifecycle_end(sid, log, "web-stop")
        if not ok:
            A.error(log, "dashboard stop (close failed)",
                    {"sid": sid, "win": win})
            return self._json({"error": "close failed"}, 502)
        return self._json({"ok": True})

    def post_rewind(self, sid):
        """Open the session's rewind/checkpoint menu (the host's `rewind`
        gesture — for Claude Code a typed `/rewind`, documented identical to the
        idle double-Esc; synthesized double-press key events opened the menu only
        ~2/3 at the best gap while the typed command opened it every time). No
        Escape pressed ⇒ no recheck.

        409 on a BUSY tab. This endpoint used to FORK there — a mid-turn
        double-Esc meant "cancel the turn and restore the message", the ⊘
        cancel button. That fork is gone: post_interrupt does the same thing
        with ONE Escape (the outcome is decided by WHEN you press, not by how
        many times — see its docstring), so the two gestures were one gesture
        all along, and the survivor is the verified one. Mid-turn the menu is
        simply unavailable: a typed `/rewind` would queue as a message.

        Every attempt is a `web-rewind` state_files row (`{win, ok, tab}`)."""
        body = self._post_guard()
        if body is None:
            return
        # refuse when the owning host can't rewind (no-op for claude_code)
        if self._caps_guard(sid, "rewind", "web-rewind"):
            return
        resolved = self._resolve_live_window(sid, "web-rewind", verb="rewind")
        if resolved is None:
            return
        row, log, sdb, fe, win, tab = resolved
        if self._dialog_open_guard(tab, log, sdb, win, "web-rewind"):
            return
        if tab in BUSY_TABS:
            A.state_file(log, sdb, "web-rewind",
                         {"win": win, "ok": False, "tab": tab,
                          "refused": "busy"})
            return self._json({"error": "session is busy — stop the turn first",
                               "tab": tab}, 409)
        res = self._gesture_host(sid).rewind(fe, win, {
            "sid": sid, "log": log, "sdb": sdb, "tab": tab,
            "action": "web-rewind", "verb": "rewind"})
        if not res.get("ok"):
            return self._json({"error": "send failed"}, 502)
        return self._json({"ok": True, "tab": tab})

    def post_rewind_to(self, sid):
        """FULL web rewind — restore the session to the checkpoint of a
        SPECIFIC prompt without touching the kitty tab (docs/dashboard.md,
        *Web rewind*): drives Claude Code's own rewind menu in the session's
        window via the host's `rewind_to` gesture (for Claude Code: a typed
        `/rewind`, screen-verified navigation, and a digit resolved from the
        parsed option labels — plugins/claude_code/rewindmenu.py).

        Body: `text` — the target prompt's full text (menu entries are its
        first line, truncation-aware); `mode` — one of the owning host's
        `rewind_modes()` ("conversation" | "both" | "code" for Claude Code);
        `ups` — the target's `up`-press
        distance from the menu's "(current)" cursor start (newer prompts
        + 1), a jump hint the text-verify scan corrects.

        409 when the tab is BUSY (mid-turn there is no rewinding — stop the
        turn first; a typed `/rewind` would just queue as a message) or when
        the step didn't verify (MenuError — menus already
        closed; `step` says which). The response's `restored` echoes `text`
        for conversation restores — Claude Code puts the rewound prompt back
        into the TUI input, so the page prefills its composer and resends
        with clear_draft, the same contract an interrupt's restore uses. Every attempt is a
        `web-rewind-to` state_files row carrying mode/ups/steps/digit (or
        the failing step), failures also an A.error."""
        body = self._post_guard()
        if body is None:
            return
        # refuse when the owning host can't rewind (no-op for claude_code)
        if self._caps_guard(sid, "rewind", "web-rewind-to"):
            return
        text = body.get("text")
        mode = body.get("mode") or "conversation"
        host = self._gesture_host(sid)
        if not isinstance(text, str) or not text.strip():
            return self._reject_input("web-rewind-to", "empty text", "empty text",
                                      {"type": type(text).__name__}, sid=sid)
        # the restore modes are the owning host's own vocabulary (its rewind
        # menu's rows), not a table here
        if mode not in host.rewind_modes():
            return self._reject_input("web-rewind-to", "bad mode", "bad mode",
                                      {"mode": mode}, sid=sid)
        try:
            ups = max(0, int(body.get("ups") or 0))
        except (TypeError, ValueError):
            ups = 0
        resolved = self._resolve_live_window(
            sid, "web-rewind-to", verb="rewind-to", extra={"mode": mode})
        if resolved is None:
            return
        row, log, sdb, fe, win, tab = resolved
        if self._dialog_open_guard(tab, log, sdb, win, "web-rewind-to"):
            return
        if tab in BUSY_TABS:
            A.state_file(log, sdb, "web-rewind-to",
                         {"win": win, "ok": False, "tab": tab, "mode": mode,
                          "step": "busy"})
            return self._json(
                {"error": "session is busy — stop or cancel it first",
                 "tab": tab}, 409)
        res = host.rewind_to(fe, win, text, mode, {
            "sid": sid, "log": log, "sdb": sdb, "tab": tab, "ups": ups,
            "action": "web-rewind-to", "verb": "rewind-to",
            "box": launch.WebBox(sid)})
        if self._gesture_declined(res, sid, "web-rewind-to", "rewind",
                                  extra={"win": win, "mode": mode}):
            return
        if not res.get("ok"):
            return self._json({"error": res.get("detail") or "rewind failed",
                               "step": res.get("step") or ""}, 409)
        return self._json({"ok": True, "mode": mode,
                           "restored": res.get("restored") or "",
                           "degraded": res.get("degraded")})

    def _resolve_live_window(self, sid, action, *, verb, extra=None):
        """The shared head of the control-plane POST handlers that TYPE INTO a
        session's window (message / command / stop / rewind / rewind-to /
        interrupt): resolve the session row + its `log`/`sdb`, the live frontend
        (503 + an A.error when none), the AUTHORITATIVE claude_session=<sid>
        window (409 when none — a fresh kitten scan, never the stale start-time
        id), and that window's tab state. Returns (row, log, sdb, fe, win, tab)
        — or None after ALREADY sending the 503/409 response (the _plan_guard
        'already responded' convention, its sibling below).

        The failure paths write the handler's OWN `web-*` state_files row so the
        audit content is preserved EXACTLY: `action` is that row's kind, `verb`
        the A.error phrase ('dashboard <verb> (no terminal)'), and `extra` the
        per-handler fields merged into the {win:'', ok:False} failure row.

        post_rename / post_migrate / post_answer stay HAND-WRITTEN: rename
        degrades (no 503/409) on BOTH a missing terminal AND a missing window,
        migrate resolves no window and interleaves an unknown-sid 404 between
        `log` and `sdb`, and answer's answer-count 400 must fire BEFORE the
        terminal check — none of which this fixed shape can host without
        changing which check responds first."""
        extra = extra or {}
        row, log, sdb = self._audit_target(sid)
        fe = launch.frontend()
        if fe is None:
            A.error(log, "dashboard %s (no terminal)" % verb, {"sid": sid})
            A.state_file(log, sdb, action, {"win": "", **extra, "ok": False})
            self._json({"error": "no terminal available"}, 503)
            return None
        win = fe.window_for_session(sid) or ""
        if not win:
            A.state_file(log, sdb, action, {"win": "", **extra, "ok": False})
            self._json({"error": "session has no live window"}, 409)
            return None
        tab = API.tab_states().get(win) or ""
        return row, log, sdb, fe, win, tab

    def _dialog_open_guard(self, tab, log, sdb, win, action):
        """Refuse an Esc-sending gesture (interrupt / rewind) when
        a MODAL DIALOG is open — the red `awaiting-command` tab means Claude is
        asking YOU (AskUserQuestion / ExitPlanMode / a permission prompt). An
        Esc there does not cancel a turn; it DECLINES/dismisses the dialog,
        which once killed the answer the user was giving through the web ask
        card ("User declined to answer questions", 2026-07-20). The dashboard's
        dedicated cards (ask/plan/confirm) are the response path, so bail with a
        409 and audit it — the same contract post_command uses on a red tab.
        Returns True when it handled (sent) the refusal; False to proceed."""
        if tab != tabs.AWAITING_COMMAND:
            return False
        A.state_file(log, sdb, action,
                     {"win": win, "ok": False, "tab": tab, "step": "dialog"})
        self._json({"error": "a dialog is open — answer it first"}, 409)
        return True
