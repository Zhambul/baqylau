# dashboard/http/post/interrupt.py — THE stop gesture's ENDPOINT: guard, resolve
# the live window, hand the press to the session's OWNING HOST, map its result.
#
# The gesture BODY moved into the hosts (P2): Claude Code's press-until-static
# loop, its queue-drain check, the escape-recheck spawn and the take-back read
# are plugins/claude_code/hostctl.py's; codex's single Esc + `turn_aborted`
# verify are plugins/codex/hostctl.py's. Both write their own `web-interrupt`
# row. What is left here is what the endpoint owns: the caps gate, the red-tab
# refusal (an Esc into an open dialog DECLINES it), the live window, and the two
# failure statuses.
from core.noaudit import load_audit
from dashboard.config import QUEUE_TABS
from dashboard.control import launch
from dashboard.read import session as rsession

A = load_audit()


class _InterruptMixin:
    """The interrupt endpoint."""

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
        the LIVE SCREEN by the host, never guessed (a host that hands nothing
        back, like codex, reports "")."""
        return self._escape_press(sid, "interrupt", "web-interrupt")

    def _escape_press(self, sid, verb, action):
        """Body of post_interrupt: guard, resolve the LIVE window, hand the
        press to the owning host, map its result. A red (awaiting-command) tab
        is a 409 before any of that: a dialog is open and the Esc would DECLINE
        it, not interrupt a turn."""
        body = self._post_guard()
        if body is None:
            return
        # refuse when the owning host can't interrupt (no-op for claude_code —
        # its `interrupt` cap is True, so this never fires)
        if self._caps_guard(sid, "interrupt", action):
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
        # The press itself, and everything that verifies it, is the host's:
        # Claude Code re-presses while the screen still animates (a vim-mode
        # first Esc only leaves INSERT), spawns the escape-recheck for the
        # mid-think cancel gap and reads the take-back off the box; codex sends
        # ONE Esc and confirms it against the `turn_aborted` record its rollout
        # writes. `last_prompt` is the read model's half of the take-back — the
        # screen says WHETHER the box holds a restored prompt, the transcript
        # says WHAT it is.
        res = self._gesture_host(sid).interrupt(fe, win, {
            "sid": sid, "log": log, "sdb": sdb, "tab": tab, "row": row,
            "action": action, "verb": verb,
            "queueing": tab in QUEUE_TABS,
            "rollout": row.get("transcript_path") or "",
            "box": launch.WebBox(sid),
            "last_prompt": rsession.last_prompt_rec(sid)})
        if self._gesture_declined(res, sid, action, "interrupt",
                                  extra={"win": win, "tab": tab}):
            return
        if not res.get("ok"):
            return self._json({"error": "send failed"}, 502)
        if res.get("stopped") is False:
            # The screen kept animating after every re-press — the turn never
            # stopped, and the gesture deliberately did NOT spawn its recheck
            # (flipping the tab green would MASK a turn that is still running).
            # Surface it so the page toasts a failure, not a phantom success.
            return self._json({"error": "interrupt not confirmed", "tab": tab},
                              502)
        # `queued`: the stop ALSO started the next turn. The host delivered the
        # message you had queued the moment the Esc landed (the terminal's own
        # behavior — the stop doesn't idle the session, it re-points it at your
        # message), so the page must not claim "your turn": the composer stays
        # in queue mode and the delivered prompt drains its ⧗ pin.
        out = {"ok": True, "tab": tab, "queued": bool(res.get("queued")),
               "restored": res.get("restored") or ""}
        if "verified" in res:
            # a host that VERIFIES its own abort (codex's turn_aborted record)
            # reports whether it saw one; the page shows the difference
            out["verified"] = bool(res.get("verified"))
        return self._json(out)
