# dashboard/http/post.py — POST routing (the control plane).
#
# The write endpoints that TYPE INTO a terminal / launch sessions: message,
# command, stop, interrupt, rename, migrate, rewind, answer, plan, ask/composer
# drafts, uploads, new-session, presence, push, dictation, clientlog. Each is
# guarded (JSON + custom header + origin) so no cross-origin page can fire them.
import base64
import re
import threading
import binascii
import os
import time
import uuid
from functools import partial
from urllib.parse import unquote, urlparse

import plugins
from core import paths as P
from core import sessionapi as API
from core.render import strip_ansi
from core import spawn as SP
from core import state as ST
from core import tabs
from core.noaudit import load_audit
from dashboard import (askdialog, clipboard, confirmdialog, dictate, plandialog,
                       prefs, rewindmenu, suggestion)
from dashboard import config
from dashboard.config import (BUSY_TABS,
                              CLIENTLOG_MAX, EFFORTS,
                              IMAGE_MIMES,
                              QUEUE_TABS,
                              _MODEL_ARG_OK, _MODEL_OK, _NAME_CTRL, _SID_OK,
                              _clip_screen)
from dashboard.control import launch
from dashboard.control.launch import launch_argv
from dashboard.read.lists import (dir_live_sessions)
from dashboard.read.mirror import (_heal_stash)
from dashboard.read.session import (_ask_pending, _plan_pending)
from dashboard.http.base import _sid
from dashboard.notify.notifier import NOTIFIER
from dashboard.notify.presence import _mark_device, _mark_viewing
from dashboard.read import session as rsession
from plugins.claude_code import transcript

A = load_audit()

# A Claude Code thinking-spinner gerund (a spinner glyph, then a word, then the
# `…` ellipsis — e.g. `✻ Sock-hopping…`). DIAGNOSTIC ONLY: it labels an
# `interrupt-probe` capture's phase; the interrupt's liveness decision is
# screen-delta, so this pattern's version-fragility is harmless.
_SPIN_RE = re.compile(r"[^\s\w]\s+\w[\w-]*…")


class _PostMixin:

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
    _SESSION_POST = {
        "message": "post_message", "command": "post_command",
        "stop": "post_stop", "interrupt": "post_interrupt",
        "rename": "post_rename", "migrate": "post_migrate",
        "rewind": "post_rewind", "rewind-to": "post_rewind_to",
        "answer": "post_answer", "ask-draft": "post_ask_draft",
        "composer-draft": "post_composer_draft",
        "composer-queue": "post_composer_queue",
        "hint-audit": "post_hint_audit", "client-fail": "post_client_fail",
        "plan-options": "post_plan_options", "plan-decision": "post_plan_decision",
        "notify": "post_notify_mute", "viewing": "post_viewing",
    }
    _FIXED_POST = {
        ("presence",): "post_presence", ("upload",): "post_upload",
        ("sessions", "new"): "post_new_session", ("ns-prefs",): "post_ns_prefs",
        ("ns-draft",): "post_ns_draft",
        ("dirs", "hide"): "post_hide_dir", ("dictate", "token"): "post_dictate_token",
        ("push", "subscribe"): "post_push_subscribe",
        ("push", "unsubscribe"): "post_push_unsubscribe",
        ("clientlog",): "post_client_log",
        ("clipboard", "files"): "post_clipboard_files",
        ("notify",): "post_notify_global",
    }

    def route_post(self, parts):
        api = parts[1:] if parts[:1] == ["api"] else None
        if api is None:
            return self._json({"error": "not found"}, 404)
        if len(api) == 3 and api[0] == "session" and _sid(api[1]) \
                and api[2] in self._SESSION_POST:
            return getattr(self, self._SESSION_POST[api[2]])(api[1])
        fixed = self._FIXED_POST.get(tuple(api))
        if fixed:
            return getattr(self, fixed)()
        return self._json({"error": "not found"}, 404)

    def post_upload(self):
        """Stage a composer ATTACHMENT (an image/screenshot the browser pasted,
        dropped, or picked, or any other file) on disk, and hand back the
        ABSOLUTE path the composer will inject as an `@path` mention (post_message
        / post_new_session). Body: {sid?, name, mime, data(base64)}.

        Transport is JSON+base64, NOT multipart: it keeps the whole _post_guard
        browser-vector defense (same-origin + custom header + read-only switch)
        with no boundary parser to write; the price is a base64 envelope, which
        UPLOAD_MAX budgets for. The bytes land under paths.UPLOADS_DIR (durable
        ~/.claude, OUTSIDE any repo working tree so `git status` stays clean),
        in a per-session subdir; this mkdir is a sanctioned control-plane write
        (gated by _post_guard/READONLY like every other mutating POST).

        docs/dashboard.md, *Web attachments*."""
        body = self._post_guard(config.UPLOAD_MAX)
        if body is None:
            return
        sid = body.get("sid")
        sid = sid if isinstance(sid, str) and _sid(sid) else ""
        # Audit target: the uploader's session when we have a sid, else the
        # GLOBAL stream — an empty log/path, exactly like web-launch/ns-prefs.
        # NOT P.mirror_log(""), which is not a global key at all: with no sid it
        # falls back to the cwd SLUG of whatever directory the dashboard process
        # was started in, so a log-less staging upload used to file its rows in
        # the audit timeline of an unrelated session that happens to run in the
        # main checkout (the reject path already used "" — the success path did
        # not, and the two disagreed).
        log, sdb = self._audit_target(sid)[1:] if sid else ("", "")
        name = body.get("name")
        mime = body.get("mime") or ""
        data_b64 = body.get("data")
        if not isinstance(name, str) or not isinstance(data_b64, str) \
                or not isinstance(mime, str):
            return self._reject_input(
                "web-upload", "bad fields", "name, mime, data required",
                {"name": name, "mime": mime}, log=log, path=sdb)
        # basename only — strip any path component a hostile name carries, and
        # fall back to a neutral stem so an empty/dotfile name can't produce a
        # bare-uuid or hidden file.
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(name)).lstrip(".")
        safe = safe[:80] or "attachment"
        try:
            raw = base64.b64decode(data_b64, validate=True)
        except (binascii.Error, ValueError):
            return self._reject_input("web-upload", "bad base64",
                                      "invalid base64", {"name": safe},
                                      log=log, path=sdb)
        if not raw:
            return self._reject_input("web-upload", "empty file", "empty file",
                                      {"name": safe}, log=log, path=sdb)
        if len(raw) > config.UPLOAD_MAX:
            return self._reject_input("web-upload", "too large",
                                      "file too large", {"bytes": len(raw)},
                                      code=413, log=log, path=sdb)
        is_image = mime in IMAGE_MIMES
        dest_dir = P.session_uploads_dir(sid)
        path = os.path.join(dest_dir, "%s-%s" % (uuid.uuid4().hex[:8], safe))
        try:
            os.makedirs(dest_dir, exist_ok=True)
            with open(path, "wb") as f:
                f.write(raw)
        except OSError as e:
            A.error(log, "dashboard upload (write failed)",
                    {"sid": sid, "name": safe, "err": str(e)})
            A.state_file(log, sdb, "web-upload",
                         {"sid": sid, "name": safe, "bytes": len(raw),
                          "ok": False})
            return self._json({"error": "could not store upload"}, 500)
        A.state_file(log, sdb, "web-upload",
                     {"sid": sid, "name": safe, "bytes": len(raw),
                      "mime": mime, "ok": True})
        return self._json({"ok": True, "path": path, "name": safe,
                           "mime": mime, "is_image": is_image})

    def _attachment_paths(self, body):
        """The vetted `@path` prefix for the delivered message text, from a POST
        body's optional `attachments` list. Each entry MUST be an absolute path
        under paths.UPLOADS_DIR (so a caller can't smuggle an arbitrary
        filesystem path into an `@`-mention) and exist on disk. Returns a list of
        absolute paths (possibly empty); silently drops anything that fails the
        checks — the message still goes, just without the bad attachment."""
        raw = body.get("attachments")
        if not isinstance(raw, list):
            return []
        root = os.path.realpath(P.UPLOADS_DIR) + os.sep
        out = []
        for p in raw:
            if not isinstance(p, str) or not p:
                continue
            rp = os.path.realpath(p)
            if (rp + os.sep).startswith(root) and os.path.isfile(rp):
                out.append(rp)
        return out

    def _with_attachments(self, text, paths):
        """Prepend `@path` mention tokens (one per attachment) to the message
        text — the TUI-native way to attach a file, delivered verbatim over the
        existing paste_text / launch-argv transport. Paths first, then a newline,
        then the typed text (mirrors the TUI's paste-then-type order). No text is
        fine: the mentions alone are a valid message."""
        if not paths:
            return text
        mentions = " ".join("@" + p for p in paths)
        return mentions + ("\n" + text if text else "")

    def post_message(self, sid):
        """Type a message into a session's kitty window (the composer). 4xx when
        the session has no window (headless/daemon) or the text is empty; 503
        when no terminal resolves; else Frontend.send_text. Every attempt is a
        `web-send` state_files row, failures also an A.error.

        `clear_draft` (bool): the TUI input already holds text the web put
        there — an interrupt that took the last message back, or a rewind that
        restored one — so the send first kills the line (Ctrl+U to start +
        Ctrl+K to end, so the cursor position doesn't matter) and then delivers
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
        if _ask_pending(sid) or _plan_pending(sid):
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
        if clear_draft:
            # kill the restored draft (both directions), settle, then paste
            fe.send_key(win, "ctrl+u")
            fe.send_key(win, "ctrl+k")
            time.sleep(config.DRAFT_CLEAR_GAP_S)
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
        clip = launch._clear_clipboard_image()
        ok = bool(fe.paste_text(win, text))
        A.state_file(log, sdb, "web-send",
                     {"win": win, "chars": len(text), "ok": ok, "tab": tab,
                      "clear_draft": clear_draft, "tui_draft": bool(pending_draft),
                      "attachments": len(attachments),
                      "clip": clip, "live": live, "queued": queued})
        if not ok:
            A.error(log, "dashboard message (send failed)",
                    {"sid": sid, "win": win})
            return self._json({"error": "send failed"}, 502)
        if pending_draft:
            launch.set_tui_draft(sid, "")     # consumed by this send
        return self._json({"ok": True, "queued": queued, "tab": tab})

    def post_command(self, sid):
        """The scoreboard's quick-command row — type one of the TUI's OWN
        slash commands into the session's window: `{"cmd": "compact"}` →
        `/compact`, `{"cmd": "model", "arg": <alias|id>}` → `/model <arg>`,
        `{"cmd": "effort", "arg": <level>}` → `/effort <arg>` (both may open
        the TUI's switch-confirm menu, auto-answered Yes below — the reply's
        `confirm` field). A FIXED
        vocabulary, 400 on anything else — the arg is validated
        (_MODEL_ARG_OK / EFFORTS) precisely because it is typed into a
        terminal, and compact takes no arg (the closed vocabulary IS the
        point; free-form text is the composer's job). Delivery matches
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
        if cmd == "compact" and not arg:
            text = "/compact"
        elif cmd == "model" and isinstance(arg, str) \
                and _MODEL_ARG_OK.match(arg):
            text = "/model " + arg
        elif cmd == "effort" and arg in EFFORTS:
            text = "/effort " + arg
        else:
            return self._reject_input("web-command", "bad cmd", "unknown command",
                                {"sid": sid, "cmd": cmd, "arg": arg})
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

    def post_interrupt(self, sid):
        """Press Escape in a session's kitty window (Frontend.send_key) — the
        TUI's own interrupt: stops the current turn in place, the session
        stays up. Distinct from post_stop, which closes the whole tab. Key
        EVENTS, not send_text bytes — a raw \\x1b never reaches a TUI in the
        kitty keyboard protocol as Escape. 409 when the session has no window
        (headless — nothing to interrupt); 503 when no terminal resolves.
        Every attempt is a `web-interrupt` state_files row, failures also an
        A.error.

        THE one stop gesture (docs/dashboard.md, *Interrupt*). There used to be
        a second button — ⊘ cancel — that pressed Escape TWICE for Claude Code's
        "cancel the turn and hand the message back". Measured 2026-07-25: a
        plain single Escape does that too. Which of the two outcomes you get is
        decided by WHEN you press, not by how many times: interrupt a turn
        that has produced nothing and Claude Code discards the prompt (by
        RE-PARENTING — see transcript._dead_uuids) and restores it to the input
        box; interrupt one that has done work and the work is kept. The press
        count never entered into it, so the second button was the same gesture
        wearing a different label.

        The response's `restored` is that handed-back message, or "" — read off
        the LIVE SCREEN (_restored_input), never guessed."""
        return self._escape_press(sid, "interrupt", "web-interrupt")

    def post_rename(self, sid):
        """Rename a session: append the `agent-name` naming record to its
        transcript (plugins.set_session_title — the /rename channel, docs/
        session-naming-findings.md) and, when a live window exists, also
        Frontend.set_tab_title so the kitty tab moves NOW (sticky — the tab
        stops following auto ai-titles; docs/dashboard.md *Web rename*).
        DELIBERATELY unlike post_message, no terminal / no window is NOT an
        error here — a parked session (or a dashboard outside kitty) still
        gets the JSONL rename and only the tab retitle degrades. Always
        appends, even mid-turn (a single atomic O_APPEND line — the tab state
        rides the audit row so a race is diagnosable). Every post-validation
        attempt is a `web-rename` state_files row, failures also an A.error."""
        body = self._post_guard()
        if body is None:
            return
        name = body.get("name")
        if not isinstance(name, str):
            return self._reject_input("web-rename", "bad name", "empty name",
                                      {"type": type(name).__name__}, sid=sid)
        name = _NAME_CTRL.sub(" ", name).strip()[:config.RENAME_MAX].strip()
        if not name:
            return self._reject_input("web-rename", "empty name", "empty name",
                                      {"raw": body.get("name")}, sid=sid)
        row, log, sdb = self._audit_target(sid)
        tpath = row.get("transcript_path") or ""
        if not tpath or not os.path.isfile(tpath):
            A.state_file(log, sdb, "web-rename",
                         {"win": "", "chars": len(name), "ok": False,
                          "reason": "no transcript"})
            return self._json({"error": "no transcript"}, 409)
        fe = launch._frontend()
        win = (fe.window_for_session(sid) or "") if fe else ""
        tab = (API.tab_states().get(win) or "") if win else ""
        try:
            appended = plugins.set_session_title(tpath, name)
        except OSError:
            A.error(log, "dashboard rename (append failed)", {"sid": sid})
            A.state_file(log, sdb, "web-rename",
                         {"win": win, "chars": len(name), "ok": False,
                          "tab": tab})
            return self._json({"error": "append failed"}, 502)
        if appended is None:        # no plugin owns the file (a codex rollout)
            A.state_file(log, sdb, "web-rename",
                         {"win": win, "chars": len(name), "ok": False,
                          "reason": "unsupported"})
            return self._json({"error": "unsupported session"}, 409)
        # DURABLE OVERRIDE: the transcript `agent-name` append is the canonical
        # channel (it reaches `claude --resume`), but that single record scrolls
        # out of session_title's 64KB tail-window in a long session and the
        # rename appears to "roll back" to the auto ai-title. Stash a durable,
        # tail-window-proof override keyed by the transcript stem so the DASHBOARD
        # title never reverts (docs/dashboard.md, *Web rename*). Best-effort like
        # every prefs write — a failure just falls back to the transcript read.
        stem = os.path.basename(tpath)
        stem = stem[:-len(".jsonl")] if stem.endswith(".jsonl") else stem
        stored = prefs.set_renamed_title(stem, name)
        override_ok = isinstance(stored, dict) and stored.get(stem) == name
        tab_retitled = bool(fe.set_tab_title(win, name)) if (fe and win) else False
        A.state_file(log, sdb, "web-rename",
                     {"win": win, "chars": len(name), "ok": True, "tab": tab,
                      "tab_retitled": tab_retitled, "override": override_ok})
        return self._json({"ok": True, "title": name,
                           "tab_retitled": tab_retitled})

    def post_migrate(self, sid):
        """Manually migrate a session to another subscription account — the
        header's ⇆ migrate button (docs/relimit.md *Manual migrate*). Spawns
        the SAME detached migrator the automatic rate-limit path uses
        (bin/claude-relimit.py: close the tab → wait for the SessionEnd park
        → `<alias> claude --resume <sid>` in a new tab; the adopt machinery
        carries the mirror history and the status-line capture flips the
        account chip), with two manual-intent differences baked into `mode=
        manual`: no auto-continue nudge (nothing was cut off — the resumed
        session opens at the prompt) and no 90% usage ceiling on the target
        (plugins.migration_target(manual=True) — an explicit click outranks
        the refuge rule). It runs the SAME fable→opus→sonnet downgrade ladder
        the automatic path does (docs/relimit.md *Model-downgrade ladder*):
        same model on another account when one has quota, else a downgrade rung
        passed through to `--model` (the current model is read off the
        transcript via plugins.context). Immediate, no confirm (user request —
        like ■ stop). Works live AND parked: a parked session skips the close
        leg and just relaunches. 404 for a sid this machine has never seen (no
        audit row, no live/parked state DB — the migrator can't tell "parked"
        from "never existed", so an unknown sid would sail through its park
        check and launch a doomed --resume tab; caught live 2026-07-19); 409
        when no account (any rung) qualifies;
        503 when no terminal resolves. Every attempt is a `web-migrate`
        state_files row, failures also an A.error."""
        body = self._post_guard()
        if body is None:
            return
        row, log, sdb = self._audit_target(sid)
        # The unknown-sid 404 deliberately runs BEFORE anything else (the
        # migrator can't tell "parked" from "never existed"), and files its row
        # with an empty PATH — a sid with no row and no DB has no state DB to
        # name, so the derived one would be a fiction.
        if not (row or os.path.isfile(P.state_db(log))
                or os.path.isfile(P.parked_db(log))):
            A.state_file(log, "", "web-migrate",
                         {"ok": False, "reason": "unknown sid"})
            return self._json({"error": "unknown session"}, 404)
        fe = launch._frontend()
        if fe is None:
            A.error(log, "dashboard migrate (no terminal)", {"sid": sid})
            A.state_file(log, sdb, "web-migrate",
                         {"ok": False, "reason": "no terminal"})
            return self._json({"error": "no terminal available"}, 503)
        cur = (API.kv_at(sdb, "account") or {}).get("slug") or ""
        # The model the session is running (off its transcript) feeds the
        # downgrade ladder (docs/relimit.md *Model-downgrade ladder*): a manual
        # ⇆ now downgrades too when no account has the current model free.
        cur_model = (plugins.context(row.get("transcript_path") or "")
                     or {}).get("model") or ""
        # Capture the picker's FULL reasoning (per-account rung/eff5h/limit-hit/
        # reject) so a manual-migrate REFUSAL is reconstructible from the DB —
        # the same subtle gap the automatic path closed with `relimit-pick`
        # (docs/relimit.md *Audit trail*); a bare "no target" is undebuggable.
        pick = {}
        target = plugins.migration_target(cur, cur_model, manual=True,
                                          explain=pick)
        if target is None:
            A.state_file(log, sdb, "web-migrate",
                         {"ok": False, "reason": "no target", "from": cur,
                          "pick": pick})
            return self._json({"error": "no other account available"}, 409)
        # target["model"] is the downgrade rung (or "" for a same-model migrate);
        # pick_target already resolved same-vs-downgrade, so forward it verbatim.
        proc = SP.spawn_detached(
            os.path.join(P.BIN, "claude-relimit.py"),
            [log, sid, target["slug"], target["alias"],
             row.get("cwd") or "", "manual", target["model"]],
            log, purpose="relimit:%s (web)" % target["slug"])
        ok = proc is not None
        A.state_file(log, sdb, "web-migrate",
                     {"ok": ok, "from": cur, "to": target["slug"],
                      "model": target["model"], "eff": target["eff"],
                      "cwd": row.get("cwd") or "", "pick": pick})
        if not ok:                       # spawn failure already audited by SP
            return self._json({"error": "migrator spawn failed"}, 502)
        return self._json({"ok": True, "to": target["slug"]})

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
            A.error(log, "dashboard rewind-to (%s)" % e.step,
                    {"sid": sid, "win": win, "mode": mode, "detail": str(e)})
            A.state_file(log, sdb, "web-rewind-to",
                         {"win": win, "ok": False, "tab": tab, "mode": mode,
                          "ups": ups, "step": e.step})
            return self._json({"error": str(e), "step": e.step}, 409)
        A.state_file(log, sdb, "web-rewind-to",
                     {"win": win, "ok": True, "tab": tab, "mode": mode,
                      "ups": ups, "steps": res["steps"],
                      "digit": res["digit"], "degraded": res["degraded"]})
        restored = text if mode in ("conversation", "both") else ""
        if restored:
            # Claude Code puts the rewound-to prompt back in the input box, so
            # the next send must REPLACE it (launch.tui_draft — the same
            # server-owned flag the interrupt's take-back sets)
            launch.set_tui_draft(sid, restored)
        return self._json({"ok": True, "mode": mode, "restored": restored,
                           "degraded": res["degraded"]})

    def _ask_stash(self, sid, body, action, *, count=True):
        """Match `body` against the session's OPEN `ask-pending` stash — the
        shared head of the two ask endpoints (post_answer drives the real
        dialog, post_ask_draft only stashes selections), and the sibling of
        `_plan_guard` for the ask side. Returns (pending, questions), or
        (None, None) after ALREADY sending the error response (the same
        'already responded' convention).

        Three refusals, in this order: no stash at all (409 — the dialog
        resolved in the terminal, or there never was one), a `tool_use_id` that
        doesn't match (409 — a NEWER question replaced it, so an answer meant
        for the old one must never be typed into the new dialog), and an
        `answers` list whose length doesn't match the questions (a 400
        `_reject_input`, `action` naming the row). `count=False` skips only the
        last one — post_answer's `chat: true` declines the questions instead of
        answering them, so it carries no answers at all."""
        pending = _ask_pending(sid)
        if not pending:
            self._json({"error": "no pending question"}, 409)
            return None, None
        if (body.get("tool_use_id") or "") != (pending.get("tool_use_id") or ""):
            self._json({"error": "ask expired — a newer question "
                        "replaced it (refresh)"}, 409)
            return None, None
        questions = pending.get("questions") or []
        answers = body.get("answers")
        if count and (not isinstance(answers, list)
                      or len(answers) != len(questions)):
            self._reject_input(
                action, "answer count",
                "answers must match the %d question%s"
                % (len(questions), "" if len(questions) == 1 else "s"),
                {"n_answers": len(answers) if isinstance(answers, list) else None,
                 "n_questions": len(questions)}, sid=sid)
            return None, None
        return pending, questions

    def post_ask_draft(self, sid):
        """Persist the UNSUBMITTED ask selections (the ask card's in-progress
        answers) to the `ask-draft` kv so another device — or the same one
        after a reload — restores them when it reopens the session (docs/
        dashboard.md, *Web ask*). This types NOTHING into the terminal: it is
        a pure state write, distinct from post_answer (which drives the real
        dialog). The session SSE re-broadcasts the draft as an `ask-draft`
        event so an already-open card on another device updates live; the
        writer suppresses its own echo via `origin`.

        Body: `tool_use_id` (must match the open `ask-pending` stash — a
        draft for a gone/replaced question is refused, 409), `answers` (a
        list aligned with the questions: {selected, other} per question),
        `origin` (an opaque per-page id, echoed back over SSE). ask_fmt.py
        clears the draft on the same boundary as `ask-pending`, so it never
        outlives its question. Best-effort: a write failure is a 500 but the
        card keeps its local state and retries on the next change."""
        body = self._post_guard()
        if body is None:
            return
        pending, questions = self._ask_stash(sid, body, "ask-draft")
        if pending is None:
            return
        answers = body.get("answers")
        # normalize each answer to a dict FIRST: `answers` is only validated for
        # length above, so a non-dict element (adversarial/malformed body) must
        # not reach `.get()`. The old inline `if isinstance(a, dict)` on the
        # `selected` sub-comprehension was inert — the iterable `a.get(...)` was
        # evaluated before that per-element filter, raising AttributeError → 500.
        clean = []
        for a in answers:
            a = a if isinstance(a, dict) else {}
            clean.append({"selected": [str(s) for s in (a.get("selected") or [])],
                          "other": str(a.get("other") or "")})
        draft = {"tool_use_id": pending.get("tool_use_id") or "",
                 "answers": clean,
                 "origin": str(body.get("origin") or "")}
        log, sdb = self._audit_target(sid)[1:]
        if not ST.kv_set_at(sdb, "ask-draft", draft):
            A.error(log, "dashboard ask-draft (write failed)", {"sid": sid})
            return self._json({"error": "draft not saved"}, 500)
        A.state_file(log, sdb, "ask-draft",
                     {"action": "write", "tool_use_id": draft["tool_use_id"],
                      "origin": draft["origin"]})
        return self._json({"ok": True})

    def post_composer_draft(self, sid):
        """Persist the UNSENT composer text (the message box's in-progress
        draft) to the `composer-draft` kv so another device — or the same one
        after a reload / a return to this session from another — restores it
        (docs/dashboard.md, *Web composer draft*). Like post_ask_draft this
        types NOTHING into the terminal: a pure state write, distinct from
        post_message (which sends). The session SSE re-broadcasts the draft as
        a `composer-draft` event so an already-open composer on another device
        updates live; the writer suppresses its own echo via `origin`.

        Body: `text` (the current draft — empty/blank DELETES the stash so the
        box clears everywhere), `origin` (an opaque per-page id, echoed back
        over SSE). Best-effort: a write failure is a 500 but the box keeps its
        local text and retries on the next change. Unlike the ask draft there
        is no tool_use_id / turn-boundary lifecycle — a message draft has no
        natural expiry, so it lives until sent or overwritten (that IS the
        'come back and it's still there' the user asked for)."""
        body = self._post_guard()
        if body is None:
            return
        text = body.get("text")
        if not isinstance(text, str):
            return self._reject_input("composer-draft", "bad text",
                                      "text must be a string",
                                      {"type": type(text).__name__}, sid=sid)
        origin = str(body.get("origin") or "")
        seq = body.get("seq")
        seq = seq if isinstance(seq, (int, float)) else 0
        log, sdb = self._audit_target(sid)[1:]
        # STALE-WRITE GUARD: a debounced save and the clear-on-send race — over a
        # slow tunnel AND, since the dashboard is a ThreadingHTTPServer, in two
        # concurrent worker threads — and can arrive out of order; an old save
        # landing after the clear would resurrect a just-sent draft (the "draft
        # didn't clear" report, 2026-07-19; the concurrent-thread variant that
        # slipped a lower-seq save past a separate read-then-write, 2026-07-22).
        # Each write carries a wall-clock `seq`; a write older than what's stored
        # is dropped so the newest state stands. The compare-and-set is ATOMIC
        # (one BEGIN IMMEDIATE — read-check-write can't be interleaved), or the
        # guard's read and its write straddle a peer thread's write. A CLEAR
        # stores a whitespace-only box as an empty-text TOMBSTONE (not a delete)
        # so its seq survives to reject a later straggler; _composer_draft reads
        # a tombstone as None.
        draft = {"text": text if text.strip() else "", "origin": origin,
                 "seq": seq}
        res = ST.kv_cas_seq_at(sdb, "composer-draft", draft)
        if res == "stale":
            A.state_file(log, sdb, "composer-draft",
                         {"action": "stale", "seq": seq, "origin": origin})
            return self._json({"ok": True, "stale": True})
        if res is None:
            A.error(log, "dashboard composer-draft (write failed)", {"sid": sid})
            return self._json({"error": "draft not saved"}, 500)
        A.state_file(log, sdb, "composer-draft",
                     {"action": "write" if text.strip() else "clear",
                      "chars": len(text), "seq": seq, "origin": origin})
        return self._json({"ok": True})

    def post_composer_queue(self, sid):
        """Persist the pending queued-message chips (the ⧗ list the composer
        shows for mid-turn messages the TUI queued but hasn't delivered) to the
        `composer-queue` kv, so a reload / another device restores them instead
        of losing the chip (the 'gone even from the queue after refresh'
        report, 2026-07-19; docs/dashboard.md, *Web composer queue*). Types
        NOTHING into the terminal — a pure state write, like the draft
        endpoints. The page sends the WHOLE current chip list on every change
        (queued, delivered-drain, ✕-hide); the SSE re-broadcasts it as a
        `composer-queue` event, the writer suppressing its own echo via
        `origin`.

        Body: `items` (a list of {text}; empty DELETES the stash), `origin`."""
        body = self._post_guard()
        if body is None:
            return
        items = body.get("items")
        if not isinstance(items, list):
            return self._reject_input("composer-queue", "bad items",
                                      "items must be a list",
                                      {"type": type(items).__name__}, sid=sid)
        # str() the filter side too, not just the value side: a non-string
        # `text` (e.g. a number in a malformed body) makes `(it.get("text") or
        # "").strip()` raise AttributeError → 500.
        clean = [{"text": str(it.get("text") or "")}
                 for it in items if isinstance(it, dict)
                 and str(it.get("text") or "").strip()]
        origin = str(body.get("origin") or "")
        log, sdb = self._audit_target(sid)[1:]
        if clean:
            if not ST.kv_set_at(sdb, "composer-queue",
                                {"items": clean, "origin": origin}):
                A.error(log, "dashboard composer-queue (write failed)",
                        {"sid": sid})
                return self._json({"error": "queue not saved"}, 500)
            A.state_file(log, sdb, "composer-queue",
                         {"action": "write", "n": len(clean), "origin": origin})
        else:
            ST.kv_del_at(sdb, "composer-queue")
            A.state_file(log, sdb, "composer-queue",
                         {"action": "remove", "origin": origin})
        return self._json({"ok": True})

    def post_hint_audit(self, sid):
        """Record one lifecycle transition of an OPTIMISTIC web action (a client
        UI change shown the instant the user acts, whose REAL confirmation
        arrives async over SSE — docs/dashboard.md, *Optimistic UI & the
        web-hint audit*) as a `web-hint` state_files row, purely for
        after-the-fact debugging. `op` says WHICH optimistic action: `composer`
        (the greyed prompt stand-in before its transcript prompt lands — the
        original), `close` (the session card greyed 'closing…' until the tab
        actually parks), `answer` (the ask card greyed until its answer's
        PostToolUse drops the stash), `plan` (same for a plan decision). All
        four are client-only DOM whose lifecycle is INVISIBLE server-side, so a
        stuck greyed state leaves no trace without this beacon. Types NOTHING
        and writes NO session state — audit-only, best-effort.

        Body: `op` — composer | close | answer | plan (default composer);
        `phase` — shown | reconciled | dropped | stale (the stuck-state watchdog
        signal); `chars` (composer only — the message length; the raw prompt
        text is deliberately NOT sent, a length + timing is enough to correlate
        with the session's `web-send` row without storing content); `wait_ms`
        (ms since the optimistic state was shown — the reconcile latency);
        `reason` (for `dropped`: queued | send-failed | failed | a dialog step).
        A bad op/phase is a 400; otherwise always 200 — a telemetry beacon must
        not surface to the page."""
        body = self._post_guard()
        if body is None:
            return
        phase = str(body.get("phase") or "")
        if phase not in ("shown", "reconciled", "dropped", "stale"):
            return self._reject_input("web-hint", "bad phase", "bad phase",
                                      {"phase": phase}, sid=sid)
        op = str(body.get("op") or "composer")
        if op not in ("composer", "close", "answer", "plan"):
            return self._reject_input("web-hint", "bad op", "bad op",
                                      {"op": op}, sid=sid)
        log, sdb = self._audit_target(sid)[1:]
        content = {"op": op, "phase": phase}
        for k in ("chars", "wait_ms"):
            v = body.get(k)
            if isinstance(v, (int, float)):
                content[k] = int(v)
        reason = body.get("reason")
        if isinstance(reason, str) and reason:
            content["reason"] = reason
        A.state_file(log, sdb, "web-hint", content)
        return self._json({"ok": True})

    def post_client_fail(self, sid):
        """Record a control-plane failure the PAGE observed but the server
        can't see — a `web-clientfail` state_files row, audit-only.

        A gesture like a composer send audits its outcome server-side BEFORE
        the HTTP response travels back (post_message writes `web-send ok:true`,
        returns 200), so a response LOST in transit (server restart, tunnel /
        proxy reset, dropped connection, a slept laptop) rejects the page's
        fetch and toasts "send failed" while the send actually SUCCEEDED — an
        outcome invisible to the audit until now (the "I saw a failed toast but
        the message went through" report). This beacon closes that blind spot:
        the page posts what IT saw, to be correlated against the paired
        `web-send`/`web-*` row.

        Body: `gesture` (send | resume | queue | … — which action the page was
        attempting), `kind` (transport = the fetch itself rejected, the
        server likely never saw the request OR its response was lost | http =
        the server returned an error status, so a paired failure row should
        exist), `error` (the error text the page had, capped), `status` (the
        HTTP status when `kind='http'`), `chars` (message length, optional).
        Types NOTHING and writes NO session state — best-effort, always 200
        unless the guard rejects (a telemetry beacon must not surface to the
        page; it also rides the SAME tunnel that may have just failed, so a
        missing row is itself expected for a total outage — the toast is the
        user-facing signal, this is only the after-the-fact breadcrumb)."""
        body = self._post_guard()
        if body is None:
            return
        gesture = str(body.get("gesture") or "")[:32]
        kind = str(body.get("kind") or "")
        if kind not in ("transport", "http"):
            kind = "transport"
        content = {"gesture": gesture, "kind": kind}
        err = body.get("error")
        if isinstance(err, str) and err:
            content["error"] = err[:200]
        for k in ("status", "chars"):
            v = body.get(k)
            if isinstance(v, (int, float)):
                content[k] = int(v)
        log, sdb = self._audit_target(sid)[1:]
        A.state_file(log, sdb, "web-clientfail", content)
        return self._json({"ok": True})

    def post_client_log(self):
        """The FRONTEND AUDIT sink (docs/dashboard.md, *Frontend audit
        (clientlog)*): record a BATCH of browser-side events — one `web-client`
        state_files row each — so the page can report what IT actually did with a
        control request the server may never have seen. This closes the whole
        blind spot behind the "still not closing" saga: a `/stop` the browser
        *tried* but that never reached a handler (dropped by the tunnel, starved
        of a connection, queued forever) left NO server trace — only a client-side
        `close.begin` with no `close.ok`/`close.fail` reveals it, and only the
        browser can write that. Distinct from the other two client beacons:
        `web-hint` tracks OPTIMISTIC-UI lifecycle (shown/reconciled/stale),
        `web-clientfail` a single observed gesture failure; `web-client` is the
        general per-gesture transport + connection + JS-error timeline they sit
        on top of.

        Body: `client` (the page's opaque CLIENT_ID — correlates a device's rows
        across a batch), `conn` (a connection-health snapshot: `online`, `vis`,
        `view`, `es` = SSE streams held open, `conn` = global stream up), and
        `events` — a list of `{t, sid, ev, …scalars}`. `ev` is a dotted name:
        `<gesture>.begin`/`.ok`/`.fail` for a tagged control POST (close | send |
        command | interrupt | rename | migrate | rewind | rewind-to | answer |
        plan | new | resume-send), `close.reconciled`; `composer.recall` (an ↑/↓
        history-recall move in the composer — *Web composer history*);
        `sse.open`/`sse.drop` per
        stream; `js.error`/`js.reject` (uncaught); `boot`/`hello`/`stale` (page +
        build lifecycle — a `boot.build` ≠ `hello.boot` mismatch = stale cached
        JS); `meta.stuck`/`meta.resolved`/`meta.fail` (session-view load + the
        launch tag-race); `launch.arm`/`launch.hit`/`launch.timeout` (the launched
        session appearing); `backlog.fail`. Each event becomes one row scoped to its own `sid`
        (so it lands in that session's timeline); a blank/invalid sid is a
        session-less row (a launch, a boot record). Only scalar fields survive,
        strings capped, at most CLIENTLOG_MAX events — a page can't stuff bulk
        into the audit. Always 200 unless the guard rejects (telemetry must not
        surface to the page); rides the same channel a failing gesture might, so a
        missing batch is itself expected for a total outage."""
        body = self._post_guard()
        if body is None:
            return
        events = body.get("events")
        if not isinstance(events, list):
            return self._json({"error": "bad events"}, 400)
        client = str(body.get("client") or "")[:40]
        device = str(body.get("device") or "")[:40]
        conn = body.get("conn") if isinstance(body.get("conn"), dict) else None
        conn = self._clip_scalars(conn) if conn else None
        for e in events[:CLIENTLOG_MAX]:
            if not isinstance(e, dict):
                continue
            ev = str(e.get("ev") or "")[:40]
            if not ev:
                continue
            esid = e.get("sid")
            esid = esid if isinstance(esid, str) and _sid(esid) else ""
            # a blank/invalid sid is a session-LESS row (a launch, a boot
            # record): the global stream, empty log/path — never a derived key.
            log, sdb = self._audit_target(esid)[1:] if esid else ("", "")
            content = {"ev": ev}
            if client:
                content["client"] = client
            if device:
                content["device"] = device
            ts = e.get("t")
            if isinstance(ts, (int, float)):
                content["t"] = int(ts)
            for k, v in self._clip_scalars(e).items():
                if k not in ("ev", "sid", "t", "client", "device"):
                    content[k] = v
            if conn:
                content["conn"] = conn
            A.state_file(log, sdb, "web-client", content)
        return self._json({"ok": True})

    def post_answer(self, sid):
        """Answer the session's OPEN AskUserQuestion dialog from the web (the
        ask card — docs/dashboard.md, *Web ask*): drives the TUI's own dialog
        with screen-verified key events (dashboard/askdialog.drive).

        Body: `tool_use_id` — must match the `ask-pending` stash (a stale
        card is refused before any key is pressed); either `chat: true` (the
        dialog's own "Chat about this" — declines + invites discussion; the
        page then focuses its composer) or `answers` — a list aligned with
        the stash's questions: {"selected": [labels…], "other": "text"} per
        question (multiSelect may combine both; single-select uses one or
        the other).

        409 on a missing/expired stash, a stash/window mismatch, or any
        dialog step that didn't verify (AskError — the dialog is left OPEN,
        never Escape-closed: Escape would DECLINE the questions; `step` says
        what failed and a retry from the card re-normalizes). Every attempt
        is a `web-answer` state_files row, failures also an A.error. The
        card itself clears via the SSE `ask` event when the answer's
        PostToolUse drops the stash — the true end-to-end signal."""
        body = self._post_guard()
        if body is None:
            return
        chat = bool(body.get("chat"))
        answers = body.get("answers")
        log, sdb = self._audit_target(sid)[1:]
        # the stash match + the answer-count 400 must BOTH fire before the
        # terminal checks below — no key may be pressed for a stale card
        pending, questions = self._ask_stash(sid, body, "web-answer",
                                            count=not chat)
        if pending is None:
            return
        fe = launch._frontend()
        if fe is None:
            A.error(log, "dashboard answer (no terminal)", {"sid": sid})
            A.state_file(log, sdb, "web-answer",
                         {"win": "", "ok": False, "chat": chat})
            return self._json({"error": "no terminal available"}, 503)
        win = fe.window_for_session(sid) or ""
        if not win:
            A.state_file(log, sdb, "web-answer",
                         {"win": "", "ok": False, "chat": chat})
            return self._json({"error": "session has no live window"}, 409)
        try:
            askdialog.drive(fe, win, questions, answers or [], chat=chat)
        except askdialog.AskError as e:
            ctx = {"sid": sid, "win": win, "chat": chat, "detail": str(e)}
            if e.screen is not None:      # the pixels the failing step saw
                ctx["screen"] = _clip_screen(e.screen)
            A.error(log, "dashboard answer (%s)" % e.step, ctx)
            A.state_file(log, sdb, "web-answer",
                         {"win": win, "ok": False, "chat": chat,
                          "step": e.step,
                          "tool_use_id": pending.get("tool_use_id") or ""})
            _heal_stash(sid, log, sdb, "ask-pending", e.step)
            return self._json({"error": str(e), "step": e.step}, 409)
        A.state_file(log, sdb, "web-answer",
                     {"win": win, "ok": True, "chat": chat,
                      "tool_use_id": pending.get("tool_use_id") or ""})
        # a PREVIEW-layout question has no typed-answer row (askdialog
        # _require_type_row), so the card routes a TYPED answer through 'Chat
        # about this' AND carries the typed text here as `message`: once the
        # dialog is dismissed (drive waited for that), deliver it as the
        # follow-up so the user's custom answer reaches the session as a
        # normal message (docs/dashboard.md, *Web ask*). Only with chat.
        msg = body.get("message")
        resp = {"ok": True, "chat": chat}
        if chat and isinstance(msg, str) and msg.strip():
            clip = launch._clear_clipboard_image()      # clipboard-image guard, as post_message
            sent = bool(fe.paste_text(win, msg))
            A.state_file(log, sdb, "web-send",
                         {"win": win, "chars": len(msg), "ok": sent,
                          "via": "ask-chat", "clip": clip})
            if not sent:
                A.error(log, "dashboard answer-chat message (send failed)",
                        {"sid": sid, "win": win})
            resp["message_sent"] = sent
        return self._json(resp)

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
        fe = launch._frontend()
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

    def _plan_guard(self, sid):
        """The shared head of the two plan endpoints: guard the POST, match
        the stash, resolve the live window. Returns (body, pending, fe, win,
        log, sdb) — or (None, …) after sending the error response."""
        none = (None,) * 6
        body = self._post_guard()
        if body is None:
            return none
        log, sdb = self._audit_target(sid)[1:]
        pending = _plan_pending(sid)
        if not pending:
            self._json({"error": "no pending plan"}, 409)
            return none
        if (body.get("tool_use_id") or "") != (pending.get("tool_use_id")
                                               or ""):
            self._json({"error": "plan expired — a newer plan replaced it "
                        "(refresh)"}, 409)
            return none
        fe = launch._frontend()
        if fe is None:
            A.error(log, "dashboard plan (no terminal)", {"sid": sid})
            self._json({"error": "no terminal available"}, 503)
            return none
        win = fe.window_for_session(sid) or ""
        if not win:
            self._json({"error": "session has no live window"}, 409)
            return none
        return body, pending, fe, win, log, sdb

    def post_plan_options(self, sid):
        """The plan card's decision buttons — the dialog's option labels VARY
        with the session's permission mode ("Yes, and bypass permissions" vs
        "Yes, and auto-accept edits"), so the page fetches them from the live
        screen (plandialog.options — read-only: no key is pressed). An `open`
        bail self-heals the stash (the dialog resolved in the terminal)."""
        body, pending, fe, win, log, sdb = self._plan_guard(sid)
        if body is None:
            return
        try:
            opts = plandialog.options(fe, win)
        except plandialog.PlanError as e:
            _heal_stash(sid, log, sdb, "plan-pending", e.step)
            return self._json({"error": str(e), "step": e.step}, 409)
        return self._json({"ok": True, "options": opts})

    def post_plan_decision(self, sid):
        """Decide the OPEN plan dialog from the web (docs/dashboard.md, *Web
        plan mode*): drives the TUI's own dialog via dashboard/plandialog.

        Body (one of, after `tool_use_id` matching the `plan-pending` stash):
        `digit` + `label` — press that decision row, verified against the
        live screen (label drift = 409, nothing pressed); `feedback` — the
        "Tell Claude what to change" row: focus, type, Enter (rejects with
        feedback; newlines collapse — single-line editor); `dismiss: true` —
        Escape, the TUI's own reject-and-keep-planning.

        409 on stash mismatch or any unverified step (PlanError — the dialog
        is left OPEN: an Escape bail would REJECT a plan the user may still
        approve; `open` bails self-heal the stash). Every attempt is a
        `web-plan` state_files row, failures also an A.error. The card
        clears via the SSE `plan` event when the stash drops (approval's
        PostToolUse, or the turn boundary after a reject)."""
        body, pending, fe, win, log, sdb = self._plan_guard(sid)
        if body is None:
            return
        tid = pending.get("tool_use_id") or ""
        # one driver call per body shape, bound to a zero-arg callable so the
        # single try/except below owns the PlanError handling for all three
        if body.get("dismiss"):
            kind, run = "dismiss", partial(plandialog.dismiss, fe, win)
        elif isinstance(body.get("feedback"), str) \
                and body["feedback"].strip():
            kind = "feedback"
            run = partial(plandialog.feedback, fe, win, body["feedback"])
        elif body.get("digit") and isinstance(body.get("label"), str):
            kind = "decide"
            run = partial(plandialog.decide, fe, win, str(body["digit"]),
                          body["label"])
        else:
            return self._reject_input(
                "web-plan", "no action",
                "need digit+label, feedback, or dismiss",
                {"keys": sorted(body)}, log=log, path=sdb)
        try:
            run()
        except plandialog.PlanError as e:
            A.error(log, "dashboard plan (%s)" % e.step,
                    {"sid": sid, "win": win, "kind": kind,
                     "detail": str(e)})
            A.state_file(log, sdb, "web-plan",
                         {"win": win, "ok": False, "kind": kind,
                          "step": e.step, "tool_use_id": tid})
            _heal_stash(sid, log, sdb, "plan-pending", e.step)
            return self._json({"error": str(e), "step": e.step}, 409)
        A.state_file(log, sdb, "web-plan",
                     {"win": win, "ok": True, "kind": kind,
                      "label": body.get("label") or "", "tool_use_id": tid})
        return self._json({"ok": True, "kind": kind})

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

    def _escape_press(self, sid, verb, action):
        """Body of post_interrupt: guard, resolve the LIVE window, press
        Escape, audit as `action`, and spawn the escape-recheck when the
        press landed on magenta. A red (awaiting-command) tab is a 409: a
        dialog is open and the Esc would DECLINE it, not interrupt a turn."""
        body = self._post_guard()
        if body is None:
            return
        # AUTHORITATIVE window (see _resolve_live_window): the pane tagged
        # claude_session=<sid>, NOT the audit row's stale start-time id (an
        # Escape into a reused id would interrupt an unrelated session — a fresh
        # scan, never the TTL memo). 503/409 each an `action` failure row.
        resolved = self._resolve_live_window(sid, action, verb=verb)
        if resolved is None:
            return
        row, log, sdb, fe, win, tab = resolved
        if self._dialog_open_guard(tab, log, sdb, win, action):
            return
        tpath, tsize = self._press_baseline(row)   # BEFORE the key lands
        # ROBUST verified interrupt (docs/dashboard.md *Interrupt*). A single
        # synthesized Escape does NOT reliably stop a busy turn here: kitty
        # reports no per-window delivery (~2/3 reliable), AND with vim editorMode
        # (the user's `editorMode: vim`) the FIRST Escape during the thinking
        # phase only leaves INSERT mode — it never reaches the interrupt handler,
        # so the turn runs to completion (measured 2026-07-24: every real
        # single-Esc interrupt on a `thinking` tab missed — a16a181f / 3d70feca —
        # while a mid-STREAM Esc landed; the throwaway diff showed the lone Esc
        # deleting `-- INSERT --` and nothing else). So press Escape, then WHILE
        # the turn is still LIVE, press again. Liveness is NOT a marker string
        # (spinner glyphs animate, gerunds vary, thinking levels differ) — it is
        # whether the screen is still CHANGING between two captures INTERRUPT_
        # RETRY_S apart: a running turn always ticks its spinner/elapsed-timer/
        # stream, a stopped one is static. Stop the instant it goes static, so an
        # idle box never gets a stray Esc. Every capture is folded into an
        # `interrupt-probe` audit row — the ground truth for any recurrence.
        # `stopped`: True = verified static (dead), False = still animating after
        # every re-press (the Esc never landed), None = idle press / unreadable.
        pre = self._screen(fe, win)
        ok = bool(fe.send_key(win, "escape"))
        attempts, stopped = 1, None
        probes = [self._phase(pre, "pre-esc")]
        if ok and tab in QUEUE_TABS:
            for _ in range(config.INTERRUPT_TRIES):
                a = self._screen(fe, win)
                time.sleep(config.INTERRUPT_RETRY_S)
                b = self._screen(fe, win)
                probes.append(self._phase(b, "post-esc%d" % attempts))
                if a is None or b is None:   # can't read the screen — stop
                    break
                if a == b:                   # static -> the turn is dead
                    stopped = True
                    break
                stopped = False              # still animating -> still live
                if fe.send_key(win, "escape"):
                    attempts += 1
        A.state_file(log, sdb, action,
                     {"win": win, "ok": ok, "tab": tab,
                      "attempts": attempts, "stopped": stopped, "probes": probes})
        if not ok:
            A.error(log, "dashboard %s (send failed)" % verb,
                    {"sid": sid, "win": win})
            return self._json({"error": "send failed"}, 502)
        if stopped is False:
            # The screen kept animating after every re-press — the turn never
            # stopped. Do NOT spawn the escape-recheck: flipping the tab green
            # would MASK a turn that is still running (exactly how the failure
            # hid). Surface it so the page toasts a failure, not a phantom
            # success — and the `interrupt-probe` row says which phase it was.
            A.error(log, "dashboard %s (not stopped)" % verb,
                    {"sid": sid, "win": win, "attempts": attempts})
            return self._json({"error": "interrupt not confirmed", "tab": tab},
                              502)
        if tab in (tabs.THINKING, tabs.WORKING):
            # An Esc killed mid-think leaves NO signal anywhere (the known
            # interrupt-watch gap) — but a WEB interrupt is itself an event,
            # so spawn the escape-recheck: flip the dead magenta green unless
            # any real signal (state movement / transcript growth) shows up
            # within its grace. Detached + audited (A.spawn); its verdict
            # lands as tab_transitions rows under DISPATCH escape-recheck.
            self._spawn_escape_recheck(fe, win, log, tpath, tsize)
        return self._json({"ok": True, "tab": tab,
                           "restored": self._restored_input(fe, win, sid, log,
                                                            sdb, action)})

    def _restored_input(self, fe, win, sid, log, sdb, action):
        """The message Claude Code HANDED BACK to its input box by the Escape
        just pressed, or "". Interrupt a turn early enough and the prompt is
        discarded and restored to the `❯` box for editing (docs/dashboard.md,
        *Interrupt*) — the terminal does it, we only need to notice, so the
        composer can mirror it instead of the user losing the text on the web
        side (the "the message went back into kitty's input but the dashboard's
        box stayed empty" report, 2026-07-25).

        Read from the LIVE SCREEN via suggestion.typed() — the same input-box
        reader the Telegram alert's "still at the keyboard" check uses, which
        returns REAL (non-faint) box content and ignores a grey ghost
        suggestion. The screen says WHETHER; the transcript says WHAT: a box
        that now holds the message we just sent is a restore, and the exact
        text (newlines and all) comes from the transcript record, because
        typed() whitespace-normalizes a wrapped box. Anything else in the box
        is the user's OWN terminal draft — left alone, never echoed into the
        composer.

        Matching is on a RESTORE_MATCH_CHARS prefix of suggestion.cmp_key —
        whitespace REMOVED, not just normalized. A message wider than the box
        (or one with its own newlines) is captured as several lines that join
        without a separator, so the words agree but the spaces never do; and
        the prefix, rather than the whole string, keeps a box that clipped the
        tail from reading as a mismatch. A miss just yields "" — the interrupt
        still succeeded, the page simply doesn't prefill."""
        last, uid = rsession._last_prompt_rec(sid)
        if not last:
            return ""
        try:
            box = suggestion.typed(fe.get_text(win, ansi=True) or "")
        except Exception:
            A.error(log, "dashboard %s (restore probe)" % action, {"win": win})
            return ""
        if not box:
            return ""
        n = config.RESTORE_MATCH_CHARS
        hit = suggestion.cmp_key(box)[:n] == suggestion.cmp_key(last)[:n]
        # FLAG the record: a taken-back prompt is orphaned in the transcript but
        # has no SIBLING until the replacement message arrives, so until then it
        # is indistinguishable on disk from a live one and the bubble came back
        # on reload. The flag is advisory — _dead_uuids drops it the moment
        # anything descends from that prompt (docs/dashboard.md, *Interrupt*).
        flagged = bool(hit) and transcript.mark_taken_back(sid, uid)
        if hit:
            # the box now holds `last` — the NEXT send must replace it, not
            # paste after it (launch.tui_draft; the `testingtesting2` bug)
            launch.set_tui_draft(sid, last)
        A.state_file(log, sdb, action,
                     {"win": win, "phase": "restore", "restored": hit,
                      "uid": uid, "flagged": flagged})
        return last if hit else ""

    def _screen(self, fe, win, why="interrupt"):
        """The window's ANSI-stripped visible text, or None (unreadable/empty).
        The unit of every screen-DELTA liveness check (the interrupt's verify
        and post_message's queued-verify) and of the interrupt's audit probe.
        `why` names the caller in the swallow row (`dashboard <why> (probe)`).
        Never raises (audit-before-swallow)."""
        try:
            raw = fe.get_text(win)
        except Exception:
            A.error("", "dashboard %s (probe)" % why, {"win": win})
            return None
        return strip_ansi(raw) if raw else None

    def _turn_live(self, fe, win):
        """Is a turn ACTUALLY running in `win`? True = yes, False = the box is
        static (idle), None = unreadable. Two ANSI-stripped captures
        QUEUE_VERIFY_GAP_S apart — the same marker-free screen-DELTA liveness
        the interrupt's verify uses (a running turn always ticks its spinner /
        elapsed timer / token stream; a stopped one is static), because no
        marker string survives: glyphs animate, gerunds vary, thinking and
        streaming phases differ.

        The tab colour is NOT a substitute: Claude Code fires no hook on cancel,
        so a terminal-side Esc-Esc leaves it frozen mid-turn. post_message calls
        this before promising `queued`; deliberately NOT folded together with
        _escape_press's own loop, which re-presses between captures."""
        a = self._screen(fe, win, why="send")
        time.sleep(config.QUEUE_VERIFY_GAP_S)
        b = self._screen(fe, win, why="send")
        if a is None or b is None:
            return None
        return a != b

    def _phase(self, screen, at):
        """Diagnostic snapshot of `screen` at capture point `at` for the
        `interrupt-probe` audit row: the phase flags (`insert` = vim `--
        INSERT --`, `toks` = the `out: N tok/s` stream footer, `spin` = a
        thinking-spinner gerund) plus a short tail. These label the ground truth
        for a recurrence — they do NOT drive the liveness decision (screen-delta
        does), so their marker-fragility is harmless."""
        if screen is None:
            return {"at": at, "read": False}
        return {"at": at,
                "insert": "-- INSERT --" in screen,
                "toks": "tok/s" in screen,
                "spin": bool(_SPIN_RE.search(screen)),
                "tail": screen[-240:]}

    @staticmethod
    def _press_baseline(row):
        """The escape-recheck's growth baseline as (transcript_path, size): the
        session's transcript and its byte size, -1 when there is no path or it
        can't be stat'd (the recheck then falls back to its own start-time
        measurement). MUST be read BEFORE the key lands, so even the
        `[Request interrupted by user]` line itself counts as growth — that
        ordering is the whole point of taking the baseline here rather than in
        the watcher, and it's the shared half of the two Escape-sending
        handlers (post_rewind's cancel-edit and _escape_press)."""
        tpath = row.get("transcript_path") or ""
        try:
            return tpath, (os.path.getsize(tpath) if tpath else -1)
        except OSError:
            return tpath, -1

    def _spawn_escape_recheck(self, fe, win, log, tpath, tsize):
        """Detached `claude-tab-status.py escape-recheck <log> <transcript>
        <press-size>` for the session's window. Env carries the window id +
        the terminal-reach vars (fe.export_env — the detached process is
        re-parented, so the ppid socket walk can't find kitty). Spawn failure
        is audited by spawn_detached; assembly failure lands its own A.error
        (the recovery not firing must never be invisible)."""
        try:
            fe.export_env()
            env = dict(os.environ)
            env["KITTY_WINDOW_ID"] = str(win)
            args = ["escape-recheck", log, tpath]
            if tsize >= 0:
                args.append(str(tsize))
            SP.spawn_detached(os.path.join(P.BIN, "claude-tab-status.py"),
                              args, log, env=env,
                              purpose="watcher:escape-recheck")
        except Exception:
            A.error(log, "dashboard interrupt (escape-recheck spawn)",
                    {"win": win})

    def post_new_session(self):
        """Launch a new session in a new tab (Frontend.launch_tab). 400 when the
        cwd isn't an existing directory or model/effort/resume/continue don't
        validate (model: one clean argv word; effort: the CLI's EFFORTS levels;
        resume: a clean session id, exclusive with continue); 503 when no
        terminal resolves; else the launch, with `--resume <sid>`/`--continue`
        and `--model`/`--effort` riding as positional "$@" words ahead of the
        prompt. The response carries the new tab's window id when the terminal
        reports one, and a _launch_wake watcher thread hurries the session's
        SSE appearance (see its block). Audited as a `web-launch` state_files
        row (no session db exists yet, so its log/path are empty; `win` = the
        launched window)."""
        body = self._post_guard()
        if body is None:
            return
        cwd = body.get("cwd")
        if not isinstance(cwd, str) or not cwd or not os.path.isdir(cwd):
            return self._reject_input("web-launch", "bad cwd",
                                "cwd is not an existing directory",
                                {"cwd": cwd})
        model, effort = body.get("model"), body.get("effort")
        if model is not None and (
                not isinstance(model, str) or not _MODEL_OK.match(model)):
            return self._reject_input("web-launch", "bad model", "invalid model",
                                {"model": model})
        if effort is not None and effort not in EFFORTS:
            return self._reject_input("web-launch", "bad effort", "invalid effort",
                                {"effort": effort})
        # resume / continue — the CLI's own conversation-pickup flags. resume
        # carries a session id (one clean argv word, same alphabet as our sid
        # routing); continue is a bare flag. Mutually exclusive, like the CLI.
        # A resumed conversation FORKS to a new sid; the existing adopt
        # machinery and the page's jump watch both handle that on their own.
        resume, cont = body.get("resume"), body.get("continue")
        if resume is not None and (
                not isinstance(resume, str) or not _SID_OK.match(resume)):
            return self._reject_input("web-launch", "bad resume", "invalid resume id",
                                {"resume": resume})
        if cont not in (None, False, True):
            return self._reject_input("web-launch", "bad continue",
                                "invalid continue", {"continue": cont})
        if resume and cont:
            return self._reject_input("web-launch", "resume+continue",
                                "resume and continue are exclusive",
                                {"resume": resume})
        # account: the switcher slug to launch under (default `claude` when
        # absent). Resolved to a registry-vetted command word — never the raw
        # value flows into the launch shell string.
        acct = body.get("account")
        cmd = plugins.account_alias(acct) if acct else "claude"
        if cmd is None:
            return self._reject_input("web-launch", "bad account", "unknown account",
                                {"account": acct})
        prompt = body.get("prompt")
        prompt = prompt if isinstance(prompt, str) else ""
        # attachments ride the launch prompt as leading @-mentions, same as the
        # live composer (covers the new-session form AND the parked "resume &
        # send" path, which both route through here). With no typed prompt, the
        # mentions alone are a valid initial prompt.
        attachments = self._attachment_paths(body)
        prompt = self._with_attachments(prompt, attachments)
        words = ((["--resume", resume] if resume else [])
                 + (["--continue"] if cont else [])
                 + (["--model", model] if model else [])
                 + (["--effort", effort] if effort else [])
                 + ([prompt] if prompt.strip() else []))
        argv = launch_argv(words, cmd)
        opts = {"cwd": cwd, "model": model or "", "effort": effort or "",
                "resume": resume or "", "cont": bool(cont),
                "account": acct or "", "attachments": len(attachments)}
        fe = launch._frontend()
        if fe is None:
            A.error("", "dashboard new-session (no terminal)", {"cwd": cwd})
            A.state_file("", "", "web-launch", dict(opts, ok=False))
            return self._json({"error": "no terminal available"}, 503)
        # Guard: never resume-launch a session that ALREADY has a live tab. A
        # second `claude --resume <sid>` would run a duplicate process against
        # the SAME transcript (two tabs, interleaved writes). The page issues a
        # resume-launch only when it believes the session is PARKED, but a
        # stale page (e.g. after the dashboard restarts and its SSE drops)
        # can misjudge a live session — this is the server-side backstop.
        # window_for_session is a fresh live kitten scan (authoritative over
        # any cached/page state); fresh and --continue launches are unaffected.
        # The page gets the live window back so it can focus/message it instead.
        if resume:
            # A resume target whose transcript .jsonl is GONE can't be resumed:
            # `claude --resume` finds no conversation and the freshly launched
            # tab exits at once — a silent dead tab the user reads as "resume
            # did nothing" (observed live on an aggregator session whose file
            # was never persisted, 2026-07-21). Reject up front when the sid's
            # KNOWN transcript path (its audit row) is absent on disk; an
            # unknown sid (no row / no path) is left to the CLI — we can't prove
            # it's broken. All accounts share ~/.claude/projects (the switcher
            # symlinks each configs/<slug>/projects to it), so the launch
            # account is irrelevant to this check.
            r_tpath = (API.session_row(resume) or {}).get("transcript_path") or ""
            if r_tpath and not os.path.isfile(r_tpath):
                A.state_file("", "", "web-launch",
                             dict(opts, ok=False, why="transcript missing"))
                return self._json(
                    {"error": "session transcript is gone — can't resume",
                     "sid": resume}, 410)
            live_win = fe.window_for_session(resume) or ""
            if live_win:
                A.state_file("", "", "web-launch",
                             dict(opts, ok=False, win=live_win))
                return self._json(
                    {"error": "session already live", "sid": resume,
                     "win": live_win}, 409)
        # the passive steal watch (see the block above the Handler class):
        # the frontmost app must be captured BEFORE the launch — a steal can
        # land before the kitten call returns. Skipped when the terminal was
        # ALREADY frontmost at click time (nothing to steal) or the frontend
        # has no OS app identity (the inert stub, off-mac).
        term = fe.app_id()
        before = launch._front_app() if term else ""
        # a launch carrying a first prompt makes Claude Code's TUI read the
        # clipboard at startup and attach any image to that auto-submitted
        # message (docs/dashboard.md *Clipboard-image guard*) — empty an image
        # clipboard first so the startup grab finds nothing. Only when there's a
        # prompt (a bare launch auto-submits nothing, so nothing to attach to).
        clip = launch._clear_clipboard_image() if prompt.strip() else False
        # launch_tab: the new window's id on success when the terminal reports
        # one (kitty prints it), bare True when it doesn't, falsy on failure.
        got = fe.launch_tab(cwd, argv)
        win = got if isinstance(got, str) else ""
        A.state_file("", "", "web-launch",
                     dict(opts, ok=bool(got), win=win, clip=clip))
        if not got:
            A.error("", "dashboard new-session (launch failed)", {"cwd": cwd})
            return self._json({"error": "launch failed"}, 502)
        # the SSE wake watch (see the block above the Handler class): hurry
        # the launched session's appearance to every connected page — and hand
        # the launching page its sid — the moment SessionStart lands.
        threading.Thread(target=launch._launch_wake, args=(win, cwd, time.time()),
                         daemon=True, name="web-launch-wake").start()
        if before and before != term:
            threading.Thread(target=launch._steal_watch, args=(before, term),
                             daemon=True, name="web-launch-steal-watch").start()
        # `win` lets the page match the launched session exactly (its jump
        # watch compares kitty_window_id); "" when the terminal didn't report
        # an id — the page falls back to its cwd heuristic.
        return self._json({"ok": True, "win": win})

    def post_ns_prefs(self):
        """Remember the new-session form's last-used {cwd, model, effort} in the
        durable GLOBAL prefs store (dashboard/prefs.py) so the next launch — on
        this device or any other pointing at this dashboard — pre-selects them
        (docs/dashboard.md, *New-session prefs*). The page calls this on a
        successful launch, exactly where it used to write localStorage; the
        BEHAVIOUR is unchanged, only the storage moved to the backend.

        Body: `cwd` (string), `model`/`effort` (validated against the same
        allowlists post_new_session uses — a bad value is dropped, never
        stored, so a corrupt pref can't later feed the launch path). Missing
        fields are simply omitted from the stored record. Best-effort: a write
        failure is a 500 but the launch itself already succeeded."""
        body = self._post_guard()
        if body is None:
            return
        rec = {}
        cwd = body.get("cwd")
        if isinstance(cwd, str) and cwd:
            rec["cwd"] = cwd
        model = body.get("model")
        if isinstance(model, str) and _MODEL_OK.match(model):
            rec["model"] = model
        effort = body.get("effort")
        if effort in EFFORTS:
            rec["effort"] = effort
        if not prefs.set("new-session", rec):
            A.error("", "dashboard ns-prefs (write failed)", {"rec": rec})
            return self._json({"error": "prefs not saved"}, 500)
        # global (no session) — audited with an empty log/path like web-launch
        A.state_file("", "", "ns-prefs", dict(rec, action="write"))
        return self._json({"ok": True})

    def post_ns_draft(self):
        """Persist the new-session form's UNSENT first prompt to the durable
        GLOBAL prefs store (docs/dashboard.md, *New-session draft*) so closing
        the form — Esc, cancel, a stray backdrop click, a reload, a switch to
        another device — never throws a half-typed prompt away; the next open
        restores it. The sibling of post_composer_draft for the one box that has
        no session to hang a `composer-draft` kv on yet, and like it this types
        NOTHING into any terminal — a pure state write.

        Body: `cwd` (WHICH directory's draft — drafts are per-directory, so two
        projects hold two half-typed prompts; the key is the page's own nsDirKey
        normalization, stored verbatim, and "" is legitimate), `text` (the
        current draft; empty/blank CLEARS that directory's), `seq` (the writer's
        wall clock; a write older than that directory's stored one is DROPPED, so
        a debounced save in flight when the launch clears can't resurrect the
        sent prompt — the same stale-write guard the composer draft carries).
        Best-effort like every prefs write (mutate_map degrades silently rather
        than raising into a request), and the page never clears its own box on
        the response — a lost save just re-saves on the next keystroke."""
        body = self._post_guard()
        if body is None:
            return
        text = body.get("text")
        if not isinstance(text, str):
            return self._reject_input("ns-draft", "bad text",
                                      "text must be a string",
                                      {"type": type(text).__name__})
        cwd = body.get("cwd", "")
        if not isinstance(cwd, str):
            return self._reject_input("ns-draft", "bad cwd",
                                      "cwd must be a string",
                                      {"type": type(cwd).__name__})
        seq = body.get("seq")
        seq = seq if isinstance(seq, (int, float)) else 0
        text = text if text.strip() else ""          # a blank box IS a clear
        rec = prefs.set_ns_draft(cwd, text, seq)
        if rec.get("stale"):
            A.state_file("", "", "ns-draft",
                         {"action": "stale", "cwd": cwd, "seq": seq})
            return self._json({"ok": True, "stale": True})
        # global (no session) — audited with an empty log/path like ns-prefs.
        # The TEXT never lands in the audit (it is the user's unsent prose, and
        # the composer draft records only its length either): the directory,
        # chars + seq are what a "my draft vanished / came back / belongs to the
        # wrong project" report needs.
        A.state_file("", "", "ns-draft",
                     {"action": "write" if text else "clear", "cwd": cwd,
                      "chars": len(text), "seq": seq})
        return self._json({"ok": True})

    def post_notify_mute(self, sid):
        """Opt a session in/out of the deferred Telegram alert (docs/dashboard.md
        *Telegram alerts*) — the header ◉/○ toggle. Body: `muted` (bool).
        Writes the durable global prefs store (dashboard/prefs.py), NOT any
        session/terminal state, so it works live AND parked. Behind _post_guard
        like every control-plane POST; audited as a `notify-mute` state_files row
        (global — empty log/path like hide-dir). Returns the flipped state."""
        body = self._post_guard()
        if body is None:
            return
        muted = body.get("muted")
        if not isinstance(muted, bool):
            return self._reject_input("notify-mute", "bad muted",
                                      "muted must be a boolean", {"muted": muted})
        prefs.set_notify_muted(sid, muted)
        A.state_file("", "", "notify-mute", {"sid": sid, "muted": muted})
        return self._json({"ok": True, "muted": muted})

    def post_notify_global(self):
        """The GLOBAL alerts master switch (docs/dashboard.md *Global alerts
        toggle*) — the list page's ◉/○ button next to "+ session". Body:
        `enabled` (bool). Writes the durable global prefs store
        (dashboard/prefs.py `notify-enabled`), NOT any session/terminal state, so
        one flip governs EVERY session — live or parked, main checkout or any git
        worktree — and OVERRIDES the per-session mutes when OFF. Behind
        _post_guard like every control-plane POST; audited as a global
        `notify-global` state_files row (empty log/path like notify-mute). Pushes
        a `notify-config` SSE event so every OTHER open page repaints its toggle
        (the functional suppression is already instant cross-device — the
        notifier reads the flag live; this only syncs the button's visual state).
        Returns the flipped state."""
        body = self._post_guard()
        if body is None:
            return
        on = body.get("enabled")
        if not isinstance(on, bool):
            return self._reject_input("notify-global", "bad enabled",
                                      "enabled must be a boolean", {"enabled": on})
        prefs.set_notify_enabled(on)
        A.state_file("", "", "notify-global", {"enabled": on})
        NOTIFIER.push("notify-config", {"enabled": on})
        return self._json({"ok": True, "enabled": on})

    def post_viewing(self, sid):
        """Presence heartbeat: the page reports it is looking at session `sid`
        RIGHT NOW (docs/dashboard.md *Telegram alerts*). The client sends it on
        a timer ONLY while the page is visible + focused + showing this session,
        so the mere arrival is the signal — it refreshes the in-memory
        `_VIEWING` deadline (`_mark_viewing`, fresh for VIEW_TTL_S) that the
        deferred Telegram alert checks at send time to tell 'watching the
        dashboard' from 'walked away'. Types NOTHING and writes NO session
        state; NOT audited per-beat (ephemeral live-only presence, like the SSE
        connection — the SUPPRESS it drives lands the notify-suppress row).
        Behind _post_guard like every control-plane POST; always 200 (an empty
        `{}` body is fine — the URL's sid IS the payload)."""
        if self._post_guard() is None:
            return
        _mark_viewing(sid)
        return self._json({"ok": True})

    def post_hide_dir(self):
        """Hide a directory group from the list page (docs/dashboard.md *Hidden
        directories*). Non-destructive: the sessions keep running, their tabs and
        toasts still fire — the group just vanishes from the crowded list until a
        session STARTED after this moment shows up in it (the client compares each
        row's started_at against the stored hide time, so 'start a new session
        there' un-hides it, terminal- or dashboard-launched). Stores {key:
        time.time()} in the durable global prefs store (dashboard/prefs.py),
        keyed by the list's group key (git.root||cwd — the page posts g.cwd,
        already that key). Behind _post_guard like every control-plane POST,
        though it writes only the dashboard's OWN prefs, never a session/terminal.

        A directory with at least one ACTIVE (live) session can't be hidden — a
        409, the authoritative guard behind the disabled ✕ (dir_live_sessions;
        the client also disables the button, but a stale page could still POST).
        Audited as a `hide-dir` state_files row (global — empty log/path like
        ns-prefs). Returns the updated map so the page reconciles S.hidden with
        the server truth."""
        body = self._post_guard()
        if body is None:
            return
        key = body.get("cwd")
        # The EMPTY string is a valid key — it is the list's "no project"
        # aggregate group (sessions with no cwd / git root), which the user can
        # hide like any other. Only a MISSING/non-string cwd (None etc.) is a bad
        # request. repr() in the audit: a reject must keep the EXACT received
        # bytes (same rule as new-session's bad cwd). len cap: a group key is a
        # path — no legitimate one runs long, and the store is not a bucket.
        if not isinstance(key, str) or len(key) > 4096:
            return self._reject_input("hide-dir", "bad key", "cwd must be a string",
                                {"cwd": key})
        # A directory with an active session can't be hidden (409). Not an input
        # error — the key is well-formed — so it's a distinct `why`, but the same
        # audited-reject shape (no errors row / errwatch chip; an expected 4xx).
        live = dir_live_sessions(key)
        if live:
            return self._reject_input(
                "hide-dir", "live session",
                "can't hide a directory with an active session",
                {"cwd": key, "live": len(live)}, code=409)
        ts = time.time()
        m = prefs.hide_dir(key, ts)
        A.state_file("", "", "hide-dir", {"key": key, "hidden_at": ts})
        return self._json({"ok": True, "hidden": m})

    def post_push_subscribe(self):
        """Register a browser's Web Push subscription (docs/dashboard.md *Web
        push*). Body: {subscription: {endpoint, keys:{p256dh, auth}}} — the exact
        PushSubscription.toJSON() the browser produced. Stored (upserted by
        endpoint) in the durable global prefs store; the Notifier fans a push out
        to every stored subscription on the deferred asking/done alert, honoring
        the per-session ○ mute. Behind _post_guard like every control-plane POST,
        though it writes only the dashboard's OWN prefs. Audited as a `web-push`
        state_files row (action subscribe)."""
        body = self._post_guard()
        if body is None:
            return
        sub = body.get("subscription")
        ep = sub.get("endpoint") if isinstance(sub, dict) else None
        keys = sub.get("keys") if isinstance(sub, dict) else None
        if not (isinstance(ep, str) and ep.startswith("https://")
                and isinstance(keys, dict) and keys.get("p256dh") and keys.get("auth")):
            return self._reject_input("web-push", "bad subscription",
                                      "subscription must carry endpoint + keys",
                                      {"has_ep": bool(ep)})
        # `device` (the browser's stable localStorage id) + `label` (a friendly
        # platform string) let the Notifier route the on-device push to the ONE
        # device you most recently used instead of every subscription. Optional
        # (a legacy client omits them → the sub is stored untagged, and routing
        # degrades to send-all for it — see _mru_push_targets).
        dev = body.get("device")
        dev = dev if isinstance(dev, str) and dev else None
        label = body.get("label")
        label = label[:60] if isinstance(label, str) and label else None
        prefs.add_push_subscription(sub, device=dev, label=label)
        A.state_file("", "", "web-push", {"action": "subscribe", "endpoint": ep[:80],
                                          "device": dev, "label": label})
        return self._json({"ok": True})

    def post_presence(self):
        """Device presence heartbeat: the page reports it is visible + focused
        RIGHT NOW on this device (docs/dashboard.md *Device routing*). The client
        sends it on a timer + on focus/reveal, from ANY view (not just a session
        — so 'you're on this device' is recorded even from the list). Body:
        {device, sid?}. `device` (the browser's stable localStorage id) stamps
        `_DEVICE_SEEN` so the on-device push routes to your most-recently-used
        device; `sid` (present only inside a session view) ALSO refreshes the
        `_VIEWING` deadline that suppresses the alert while you watch that
        session — folding the old per-session viewing beat into this one. Types
        NOTHING and writes NO session state; NOT audited per-beat (ephemeral
        presence, like the SSE connection). Behind _post_guard; always 200."""
        body = self._post_guard()
        if body is None:
            return
        dev = body.get("device")
        if isinstance(dev, str) and dev:
            _mark_device(dev)
        sid = body.get("sid")
        if isinstance(sid, str) and sid:
            _mark_viewing(sid)
        return self._json({"ok": True})

    def post_push_unsubscribe(self):
        """Drop a browser's Web Push subscription (docs/dashboard.md *Web push*)
        — the opt-out twin of subscribe. Body: {endpoint}. Idempotent (a missing
        endpoint just no-ops). Audited as a `web-push` state_files row (action
        unsubscribe)."""
        body = self._post_guard()
        if body is None:
            return
        ep = body.get("endpoint")
        if not isinstance(ep, str) or not ep:
            return self._reject_input("web-push", "bad endpoint",
                                      "endpoint required", {})
        prefs.remove_push_subscription(ep)
        A.state_file("", "", "web-push", {"action": "unsubscribe", "endpoint": ep[:80]})
        return self._json({"ok": True})

    def post_clipboard_files(self):
        """Resolve the FULL PATHS of files the browser just pasted as zero-byte
        promises (docs/dashboard.md *Web attachments* → *Promise-only files*).
        Body: {names: [basename, …], sid?} → {paths: [abs, …]}.

        The page cannot answer this itself: a pasted `File` carries a BASENAME
        and nothing else (the web platform never exposes a filesystem path),
        while the pasteboard's path-bearing flavors are hidden from script. The
        server shares the pasteboard with kitty, so it reads what kitty reads —
        this endpoint is the whole reason the web composer can match the TUI.

        `clipboard.match` returns paths ONLY when their basenames are exactly
        what the caller reported, so a remote device (whose clipboard is not
        this Mac's) can never be handed an unrelated host path. A miss is a
        200 with `paths: []`, not an error — "the clipboard moved on" is an
        ordinary outcome and the page falls back to the bare name.

        Read-only apart from its audit row: nothing is written, nothing is
        staged, no terminal is touched. It stays behind _post_guard anyway —
        it reads local machine state on a page's say-so, which is exactly what
        the browser-vector defense is for."""
        body = self._post_guard()
        if body is None:
            return
        sid = body.get("sid")
        sid = sid if isinstance(sid, str) and _sid(sid) else ""
        log, sdb = self._audit_target(sid)[1:] if sid else ("", "")
        names = body.get("names")
        if not isinstance(names, list) or not names \
                or not all(isinstance(n, str) for n in names):
            return self._reject_input(
                "web-clipboard", "bad names", "names required",
                {"names": names}, log=log, path=sdb)
        names = [os.path.basename(n) for n in names[:clipboard.FILES_MAX]]
        paths = clipboard.match(names)
        # The paths ARE the diagnostic here ("it pasted the wrong file" is
        # otherwise unanswerable), and a mismatch records what was asked for so
        # a phone-vs-Mac clipboard divergence is visible as such.
        A.state_file(log, sdb, "web-clipboard",
                     {"sid": sid, "names": names, "matched": len(paths),
                      "paths": paths})
        return self._json({"paths": paths})

    def post_dictate_token(self):
        """Mint a short-lived Deepgram grant for the browser's DIRECT wss
        connection (docs/dashboard.md *Web dictation* — the stdlib server
        can't speak WebSocket and must never see audio, so its whole role is
        this trade: on-disk API key → ~30s single-purpose JWT). The response
        carries the token plus the fully-assembled listen URL (model +
        keyterms server-side; the client contributes only its AudioContext
        sample rate). Behind _post_guard like every control-plane POST — on
        READONLY days dictation is off exactly like the composer it feeds.
        Every attempt is a `web-dictate` state_files row (no sid — the
        new-session form dictates too), failures also an A.error. The API
        key never appears in a response or an audit row."""
        body = self._post_guard()
        if body is None:
            return
        rate = body.get("sample_rate")
        if not isinstance(rate, int) or isinstance(rate, bool) \
                or not (dictate.SAMPLE_RATE_MIN <= rate
                        <= dictate.SAMPLE_RATE_MAX):
            A.state_file("", "", "web-dictate",
                         {"ok": False, "why": "bad-rate",
                          "rate": repr(rate)[:40]})
            return self._json({"error": "bad sample_rate"}, 400)
        if not dictate.available():
            # a race fallback only — the page hides the mic button when the
            # /api/dictate probe says unavailable
            A.state_file("", "", "web-dictate", {"ok": False, "why": "no-key"})
            return self._json({"error": "no deepgram key configured"}, 501)
        # optional cwd — keys the PROJECT vocabulary layer (the composer sends
        # its session's cwd, the new-session form its typed dir). A non-string
        # or non-directory degrades to global-only, never an error — the same
        # contract as /api/commands, and for the same reason (arbitrary
        # sessions' dirs come and go).
        cwd = body.get("cwd")
        if not isinstance(cwd, str) or not os.path.isdir(cwd):
            cwd = ""
        try:
            tok = dictate.grant()
        except Exception as e:
            A.error("", "dashboard dictate (grant failed)",
                    {"err": ("%s: %s" % (type(e).__name__, e))[:200]})
            A.state_file("", "", "web-dictate", {"ok": False, "why": "grant"})
            return self._json({"error": "token grant failed"}, 502)
        terms = dictate.keyterms(cwd)
        url = dictate.ws_url(rate, terms)
        A.state_file("", "", "web-dictate",
                     {"ok": True, "rate": rate, "cwd": cwd,
                      "keyterms": len(terms)})
        return self._json({"token": tok["access_token"],
                           "expires_in": tok.get("expires_in"),
                           "ws_url": url})
