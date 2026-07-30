# dashboard/http/post/typing.py — the control-plane POSTs that TYPE INTO a
# session's kitty window: message, quick command, stop (close the tab), and the
# two rewind gestures. Every one of them resolves the AUTHORITATIVE live window
# first (_resolve_live_window) and audits its own `web-*` state_files row.
import time

from core import sessionapi as API
from core import tabs
from core.noaudit import load_audit
from dashboard import (confirmdialog, rewindmenu)
from dashboard.config import (BUSY_TABS,
                              EFFORTS,
                              QUEUE_TABS,
                              MODEL_ARG_OK)
from dashboard.control import launch
from dashboard.read.session import (ask_pending, plan_pending)
from core.screendrive import clip_screen

A = load_audit()

DRAFT_CLEAR_GAP_S = 0.15           # settle between killing the restored draft
#                                    (ctrl+u/k) and the bracketed paste of the
#                                    edited resend (post_message clear_draft).
#                                    Read only here, so it lives with its reader
#                                    rather than in the shared knob registry —
#                                    see the note in http/post/interrupt.py.
DRAFT_CLEAR_LINES_MAX = 50         # ceiling on the per-line kill loop below —
#                                    a corrupt/huge stash must not turn into an
#                                    unbounded keystroke storm at the terminal.

# The quick-command → host CAPABILITY key each command is gated on
# (_caps_guard). The argless auto-rename is deliberately absent: rename works
# through the transcript on a parked session, and this phase does not gate it.
CAP_BY_CMD = {"compact": "compact", "model": "model", "effort": "effort"}


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

        `clear_draft` (bool): the TUI input already holds text the web put
        there — an interrupt that took the last message back, or a rewind that
        restored one — so the send first kills that draft (Ctrl+U to start +
        Ctrl+K to end per line, a backspace between lines — the stash's line
        count drives the loop, so a multi-line take-back dies whole; the
        cursor position within a line doesn't matter) and then delivers
        the text as a BRACKETED PASTE (paste_text): a raw send into the
        just-cleared input drops leading bytes (measured — the mangle), an
        atomic paste doesn't. This is what lets you edit AND resend from the
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
        text = self._with_attachments(text, attachments)
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
        live = self._turn_live(fe, win) if tab in QUEUE_TABS else None
        queued = tab in QUEUE_TABS and live is not False
        draft_lines = 0
        if clear_draft:
            # kill the restored draft, settle, then paste. Ctrl+U/Ctrl+K clear
            # ONE line, and a take-back can hold a MULTI-LINE draft (session
            # 8b9f870b, 2026-07-29: a 3-line message came back, only its last
            # line died, and the resend glued onto the two survivors) — the
            # stash knows the exact text, so kill one line per newline, a
            # backspace between kills consuming the newline to hop up a line.
            # The cursor sits on the LAST line after a restore; a body-flag-only
            # clear (no stash) keeps the historical single-line kill.
            draft_lines = pending_draft.count("\n") + 1 if pending_draft else 1
            for i in range(min(draft_lines, DRAFT_CLEAR_LINES_MAX)):
                if i:
                    fe.send_key(win, "backspace")
                fe.send_key(win, "ctrl+u")
                fe.send_key(win, "ctrl+k")
            time.sleep(DRAFT_CLEAR_GAP_S)
        # ALWAYS a bracketed paste, not a raw send: a raw send is delivered as
        # fast individual keystrokes and the TUI drops some depending on its
        # input state (reported live: "test" arrived as "t"; measured 8/8
        # clean for a bracketed paste, flaky for raw). The trailing CR is a
        # separate keystroke OUTSIDE the paste, so it still submits — and a
        # multi-line composer message pastes atomically instead of its internal
        # newlines submitting it early.
        # empty an IMAGE clipboard first — a bracketed paste makes Claude Code
        # attach whatever image is on the board (docs/dashboard.md *Clipboard-
        # image guard*); no-op on a text clipboard / off macOS.
        clip = launch.clear_clipboard_image()
        launch.note_send(sid)      # our paste is about to sit in the box for a
        #                            beat — the draft sync must not read it back
        ok = bool(fe.paste_text(win, text))
        A.state_file(log, sdb, "web-send",
                     {"win": win, "chars": len(text), "ok": ok, "tab": tab,
                      "clear_draft": clear_draft, "tui_draft": bool(pending_draft),
                      "draft_lines": draft_lines,
                      "attachments": len(attachments),
                      "clip": clip, "live": live, "queued": queued})
        if not ok:
            A.error(log, "dashboard message (send failed)",
                    {"sid": sid, "win": win})
            return self._json({"error": "send failed"}, 502)
        if pending_draft and not launch.set_tui_draft(sid, ""):
            # a stale flag only costs an extra Ctrl+U/K on an empty line, but
            # it means the STASH is broken — the same write path the take-back
            # depends on, so surface it
            A.error(log, "dashboard message (tui-draft clear)",
                    {"sid": sid, "win": win})
        return self._json({"ok": True, "queued": queued, "tab": tab})

    def post_command(self, sid):
        """The scoreboard's quick-command row — type one of the TUI's OWN
        slash commands into the session's window: `{"cmd": "compact"}` →
        `/compact`, `{"cmd": "model", "arg": <alias|id>}` → `/model <arg>`,
        `{"cmd": "effort", "arg": <level>}` → `/effort <arg>` (both may open
        the TUI's switch-confirm menu, auto-answered Yes below — the reply's
        `confirm` field), `{"cmd": "rename"}` → `/rename` (argless — bare
        `/rename` makes Claude Code GENERATE the title itself, the web
        auto-rename; a NAMED rename never comes through here, post_rename's
        transcript append works parked too). A FIXED
        vocabulary, 400 on anything else — the arg is validated
        (MODEL_ARG_OK / EFFORTS) precisely because it is typed into a
        terminal, and compact/rename take no arg (the closed vocabulary IS
        the point; free-form text is the composer's job). Delivery matches
        post_message (bracketed paste + CR via the live claude_session
        window), so mid-turn the command lands in the TUI's message queue and
        runs at the turn boundary (`queued` in the reply) — but a RED tab
        (awaiting-command: a modal dialog is up) is a 409: pasted text would
        land IN the dialog, its digits deciding it. Every attempt is a
        `web-command` state_files row, failures also an A.error."""
        body = self._post_guard()
        if body is None:
            return
        cmd, arg = body.get("cmd"), body.get("arg")
        # a NON-claude host (codex) drives model/effort through its OWN gesture
        # (an interactive /model picker, not a `/model <arg>` paste), so its arg
        # is validated by the live picker, not Claude's MODEL_ARG_OK/EFFORTS.
        host = self._gesture_host(sid)
        argful = isinstance(arg, str) and bool(arg)
        if cmd == "compact" and not arg:
            text = "/compact"
        elif cmd == "rename" and not arg:
            text = "/rename"
        elif cmd == "model" and argful and (host is not None
                                            or MODEL_ARG_OK.match(arg)):
            text = "/model " + arg
        elif cmd == "effort" and ((host is not None and argful)
                                  or arg in EFFORTS):
            text = "/effort " + arg
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
        # NON-claude host (codex) drives the command through its own gesture:
        # `compact` pastes codex's /compact, `model`/`effort` drive codex's
        # interactive /model picker (there is no /model <arg> or /effort to
        # paste). A claude_code / unprovable session returns None (host resolved
        # once, above) and the byte-identical inline paste path below runs
        # unchanged.
        if host is not None:
            if cmd == "compact":
                return self._host_compact(host, sid, log, sdb, fe, win, tab)
            if cmd in ("model", "effort"):
                return self._host_model(host, sid, cmd, arg, log, sdb, fe, win,
                                        tab)
        # the ONE slash-command channel: a bracketed paste (mode-proof — a raw
        # typed command is vim KEYSTROKES in a NORMAL-mode box) + the clipboard
        # -image guard that a paste requires (launch.type_command)
        ok, clip = launch.type_command(fe, win, text)
        A.state_file(log, sdb, "web-command",
                     {"win": win, "cmd": cmd, "arg": arg or "", "ok": ok,
                      "tab": tab, "clip": clip})
        if not ok:
            A.error(log, "dashboard command (send failed)",
                    {"sid": sid, "win": win, "cmd": cmd})
            return self._json({"error": "send failed"}, 502)
        res = {"ok": True, "queued": tab in QUEUE_TABS, "tab": tab}
        if cmd in ("model", "effort") and tab not in QUEUE_TABS:
            # newer TUI builds interpose a Yes/No switch-confirm menu (the
            # prompt-cache warning) instead of applying outright — unanswered
            # it makes the click look dead, so press its own Yes (the button
            # IS the consent), screen-verified: dashboard/confirmdialog.py.
            # Mid-turn (queued) the command only runs at the turn boundary,
            # so there is no menu to wait for here — an unanswered late menu
            # surfaces as the red-tab notification.
            try:
                c = confirmdialog.confirm(fe, win)
                res["confirm"] = "confirmed" if c["dialog"] else "none"
            except Exception as e:      # ConfirmError or a frontend hiccup —
                # the menu (if any) is left open for the terminal user
                A.error(log, "dashboard command (confirm failed)",
                        {"sid": sid, "win": win, "cmd": cmd, "err": str(e)})
                res["confirm"] = "failed"
            A.state_file(log, sdb, "web-command-confirm",
                         {"win": win, "cmd": cmd,
                          "confirm": res["confirm"]})
        return self._json(res)

    def _host_compact(self, host, sid, log, sdb, fe, win, tab):
        """Route /compact through a NON-claude host's gesture (codex pastes its
        own `/compact`). Writes the canonical `web-command` row (host/status/cid
        alongside cmd) and shapes the reply like the inline path — no confirm menu
        (that is a Claude prompt-cache prompt), and `queued` when mid-turn."""
        res = host.compact(fe, win, {"sid": sid, "log": log, "sdb": sdb})
        ok = bool(res.get("ok"))
        A.state_file(log, sdb, "web-command",
                     {"win": win, "cmd": "compact", "arg": "", "ok": ok,
                      "tab": tab, "host": host.name, "status": res.get("status"),
                      "cid": res.get("cid")})
        if not ok:
            A.error(log, "dashboard command (%s compact send failed)" % host.name,
                    {"sid": sid, "win": win})
            return self._json({"error": "send failed"}, 502)
        return self._json({"ok": True, "queued": tab in QUEUE_TABS, "tab": tab})

    def _host_model(self, host, sid, cmd, arg, log, sdb, fe, win, tab):
        """Route /model or /effort through a NON-claude host's gesture (codex
        drives its INTERACTIVE 3-step picker, which sets model + reasoning level
        together). No confirm menu (that is Claude's prompt-cache prompt — the
        gesture screen-verifies its own steps). Writes the canonical `web-command`
        row (host/status/cid) and shapes the reply like the inline path. A picker
        that can't be driven mid-turn is unlikely (codex refuses `/model` while a
        turn runs), so this is not queued — a failure is a 502."""
        ctx = {"sid": sid, "log": log, "sdb": sdb}
        res = host.model(fe, win, arg, ctx) if cmd == "model" \
            else host.effort(fe, win, arg, ctx)
        ok = bool(res.get("ok"))
        A.state_file(log, sdb, "web-command",
                     {"win": win, "cmd": cmd, "arg": arg or "", "ok": ok,
                      "tab": tab, "host": host.name, "status": res.get("status"),
                      "cid": res.get("cid"), "step": res.get("step") or ""})
        if not ok:
            A.error(log, "dashboard command (%s %s: %s)"
                    % (host.name, cmd, res.get("step") or "failed"),
                    {"sid": sid, "win": win, "detail": res.get("detail") or ""})
            return self._json({"error": res.get("detail") or "switch failed",
                               "step": res.get("step") or ""}, 502)
        return self._json({"ok": True, "queued": False, "tab": tab})

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
        if not ok:
            A.error(log, "dashboard stop (close failed)",
                    {"sid": sid, "win": win})
            return self._json({"error": "close failed"}, 502)
        return self._json({"ok": True})

    def post_rewind(self, sid):
        """Open Claude Code's rewind/checkpoint menu by TYPING `/rewind`
        (documented identical to the idle double-Esc; synthesized double-press
        key events opened the menu only ~2/3 at the best gap while the typed
        command opened it every time). No Escape pressed ⇒ no recheck.

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
        ok, clip = launch.type_command(fe, win, "/rewind")
        A.state_file(log, sdb, "web-rewind",
                     {"win": win, "ok": ok, "tab": tab, "clip": clip})
        if not ok:
            A.error(log, "dashboard rewind (send failed)",
                    {"sid": sid, "win": win})
            return self._json({"error": "send failed"}, 502)
        return self._json({"ok": True, "tab": tab})

    def post_rewind_to(self, sid):
        """FULL web rewind — restore the session to the checkpoint of a
        SPECIFIC prompt without touching the kitty tab (docs/dashboard.md,
        *Web rewind*): drives Claude Code's own rewind menu in the session's
        window via dashboard/rewindmenu.drive (typed `/rewind`, screen-
        verified navigation, digit resolved from the parsed option labels).

        Body: `text` — the target prompt's full text (menu entries are its
        first line, truncation-aware); `mode` — "conversation" | "both" |
        "code" (rewindmenu.MODE_LABELS); `ups` — the target's `up`-press
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
        if not isinstance(text, str) or not text.strip():
            return self._reject_input("web-rewind-to", "empty text", "empty text",
                                      {"type": type(text).__name__}, sid=sid)
        if mode not in rewindmenu.MODE_LABELS:
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
        try:
            res = rewindmenu.drive(fe, win, text, mode, ups=ups)
        except rewindmenu.MenuError as e:
            # the screen the failing step gave up on, clipped — without it a
            # `step: "open"` row cannot tell "the menu never opened" from "our
            # marker stopped matching a menu that did" (the 2026-07-25 drift).
            # It rides ONLY the errors row (context is untruncated there): put
            # in the state_files row too, it blew A.state_file's 2000-byte cap
            # and the truncated JSON crashed every json_extract anomaly query
            # over state_files.content (2026-07-29, session 69caa362).
            seen = clip_screen(e.screen) if e.screen else ""
            A.error(log, "dashboard rewind-to (%s)" % e.step,
                    {"sid": sid, "win": win, "mode": mode, "detail": str(e),
                     "screen": seen})
            A.state_file(log, sdb, "web-rewind-to",
                         {"win": win, "ok": False, "tab": tab, "mode": mode,
                          "ups": ups, "step": e.step})
            return self._json({"error": str(e), "step": e.step}, 409)
        A.state_file(log, sdb, "web-rewind-to",
                     {"win": win, "ok": True, "tab": tab, "mode": mode,
                      "ups": ups, "steps": res["steps"],
                      "digit": res["digit"], "degraded": res["degraded"]})
        restored = text if mode in ("conversation", "both") else ""
        if restored and not launch.set_tui_draft(sid, restored):
            # Claude Code puts the rewound-to prompt back in the input box, so
            # the next send must REPLACE it (launch.tui_draft — the same
            # server-owned flag the interrupt's take-back sets)
            A.error(log, "dashboard rewind-to (tui-draft stash)",
                    {"sid": sid, "win": win})
        return self._json({"ok": True, "mode": mode, "restored": restored,
                           "degraded": res["degraded"]})

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
