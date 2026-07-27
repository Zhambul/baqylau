# dashboard/http/post/interrupt.py — THE stop gesture: press Escape in a
# session's window, verify it landed by SCREEN DELTA (no marker string survives
# Claude Code's versions), report back the message the TUI handed to its input
# box, and spawn the escape-recheck for the mid-thinking cancel gap.
import re
import os
import time

from core import paths as P
from core.render import strip_ansi
from core import spawn as SP
from core import tabs
from core.noaudit import load_audit
from dashboard import (suggestion)
from dashboard.config import (QUEUE_TABS)
from dashboard.control import launch
from dashboard.read import session as rsession
from plugins.claude_code import transcript

A = load_audit()

# The gesture's own TUNING lives here, with its one reader, not in
# dashboard/config.py. That module is the knob REGISTRY for facts the whole tier
# shares (or that the PAGE must know, via /api/limits) — not a home for every
# constant in the package. Nothing outside this module reads the four below, so
# they follow the same rule presence.py's VIEW_TTL_S does: the owner keeps its
# own knob, and a test patches the owner (`DS.post_interrupt.X`).

QUEUE_VERIFY_GAP_S = 0.5           # gap between the TWO screen captures whose
#                                    equality decides "is a turn REALLY running"
#                                    before post_message promises `queued`. The
#                                    tab colour alone cannot promise it: Claude
#                                    Code fires NO hook on cancel, so a turn
#                                    cancelled AT THE TERMINAL (Esc-Esc) leaves
#                                    the tab frozen on magenta and the send
#                                    reports `queued` for a message the idle TUI
#                                    submits instantly — pinning a ⧗ chip no
#                                    prompt will ever drain (session bdeca061,
#                                    2026-07-25). Same marker-free screen-DELTA
#                                    liveness as the interrupt's verify (see
#                                    INTERRUPT_RETRY_S for why no string is
#                                    safe), and the same value for the same
#                                    reason: a running turn animates its
#                                    spinner/elapsed-timer/stream within it at
#                                    every thinking level. Paid only on a
#                                    QUEUE_TABS send, where the message is
#                                    queueing anyway.

# Interrupt verification (post_interrupt / _escape_press). A single synthesized
# Escape via `kitten @ send-key` is only ~2/3 reliable (kitty reports no
# per-window delivery), so a blind press silently misses — a fresh web-launched
# turn ran to completion despite ok:true (2026-07-24, session a16a181f). A
# BUSY-tab interrupt is now VERIFIED by screen delta and re-pressed WHILE the
# turn is still live — but never on an idle box (a stray Esc there could open
# /rewind). INTERRUPT_RETRY_S sits well above the TUI's own ~150 ms double-Esc
# detection window, so two spaced retries never read as a double-Esc.
INTERRUPT_TRIES = 4                # re-press passes on a still-live turn (a vim
#                                    editorMode thinking-phase Esc only exits
#                                    INSERT, so ≥2 presses are needed; extra
#                                    headroom for the ~2/3 send-key reliability)
INTERRUPT_RETRY_S = 0.5            # gap between the TWO screen captures whose
#                                    equality decides "is the turn still live"
#                                    (also the beat between re-presses). A
#                                    running Claude Code turn animates its
#                                    spinner/elapsed-timer/stream within this
#                                    window at EVERY thinking level; a stopped
#                                    one is static. This screen-DELTA liveness
#                                    deliberately replaces the earlier
#                                    marker-string match (`esc to interrupt` /
#                                    `tok/s`): glyphs animate, gerunds vary, and
#                                    the thinking vs streaming phases differ, so
#                                    no fixed literal is robust — but "the screen
#                                    is still changing" is (docs/dashboard.md
#                                    *Interrupt*, CLAUDE.md *Experimenting*).

RESTORE_MATCH_CHARS = 40           # prefix length compared when deciding
#                                    whether the input box now holds the message
#                                    the just-pressed Escape took back
#                                    (_restored_input, over suggestion.cmp_key —
#                                    whitespace REMOVED, so the box's wrap points
#                                    can't matter). A prefix, not the whole
#                                    string, so a box that clipped a long tail
#                                    still matches; long enough that two real
#                                    prompts don't collide.

# A Claude Code thinking-spinner gerund (a spinner glyph, then a word, then the
# `…` ellipsis — e.g. `✻ Sock-hopping…`). DIAGNOSTIC ONLY: it labels an
# `interrupt-probe` capture's phase; the interrupt's liveness decision is
# screen-delta, so this pattern's version-fragility is harmless.
_SPIN_RE = re.compile(r"[^\s\w]\s+\w[\w-]*…")


class _InterruptMixin:
    """The interrupt gesture and its screen probes.

    `_screen` is the capture unit and `_turn_live` the two-capture verdict
    post_message's `queued` promise uses; `_escape_press` keeps its OWN capture
    pair because it re-presses BETWEEN them (deliberately not unified)."""

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
        attempts, stopped, drained = 1, None, ""
        probes = [self._phase(pre, "pre-esc")]
        if ok and tab in QUEUE_TABS:
            for _ in range(INTERRUPT_TRIES):
                a = self._screen(fe, win)
                time.sleep(INTERRUPT_RETRY_S)
                b = self._screen(fe, win)
                probes.append(self._phase(b, "post-esc%d" % attempts))
                if a is None or b is None:   # can't read the screen — stop
                    break
                # A QUEUED MESSAGE outranks the screen. Claude Code delivers a
                # mid-turn message the instant the turn ends, so the Esc that
                # LANDED starts a new turn within milliseconds — the screen goes
                # on animating and a screen-only verdict reads "still live" and
                # presses again, interrupting the delivered message and handing
                # it back to the input box, where the web can't see it (measured
                # 2026-07-27, session 3266f418: 4 Escapes, the queued prompt
                # taken back, the user re-sending it by hand — and the leftover
                # in the box glued onto the resend). The queue records are the
                # tell the screen doesn't have (transcript.queue_drained), and
                # the queue only drains at a turn BOUNDARY: the turn is over.
                drained = transcript.queue_drained(tpath, tsize)
                if drained or a == b:        # boundary / static -> the turn is dead
                    stopped = True
                    break
                stopped = False              # still animating -> still live
                if fe.send_key(win, "escape"):
                    attempts += 1
        A.state_file(log, sdb, action,
                     {"win": win, "ok": ok, "tab": tab, "attempts": attempts,
                      "stopped": stopped, "drained": drained, "probes": probes})
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
        # `queued`: the stop ALSO started the next turn. Claude Code delivered
        # the message you had queued the moment the Esc landed (the terminal's
        # own behavior — the stop doesn't idle the session, it re-points it at
        # your message), so the page must not claim "your turn": the composer
        # stays in queue mode and the delivered prompt drains its ⧗ pin.
        return self._json({"ok": True, "tab": tab, "queued": bool(drained),
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
        last, uid = rsession.last_prompt_rec(sid)
        if not last:
            return ""
        try:
            box = suggestion.typed(fe.get_text(win, ansi=True) or "")
        except Exception:
            A.error(log, "dashboard %s (restore probe)" % action, {"win": win})
            return ""
        if not box:
            return ""
        n = RESTORE_MATCH_CHARS
        hit = suggestion.cmp_key(box)[:n] == suggestion.cmp_key(last)[:n]
        # FLAG the record: a taken-back prompt is orphaned in the transcript but
        # has no SIBLING until the replacement message arrives, so until then it
        # is indistinguishable on disk from a live one and the bubble came back
        # on reload. The flag is advisory — _dead_uuids drops it the moment
        # anything descends from that prompt (docs/dashboard.md, *Interrupt*).
        flagged = bool(hit) and transcript.mark_taken_back(sid, uid)
        # the box now holds `last` — the NEXT send must replace it, not paste
        # after it (launch.tui_draft; the `testingtesting2` bug)
        noted = bool(hit) and launch.set_tui_draft(sid, last)
        A.state_file(log, sdb, action,
                     {"win": win, "phase": "restore", "restored": hit,
                      "uid": uid, "flagged": flagged, "noted": noted})
        if hit and not (flagged and noted):
            # both stashes are what make the take-back survive a reload, so a
            # failed write is a REAL defect, not a degrade — audit it rather
            # than reporting a success the row can't back up (the rows lied
            # once already: kv_set from a request thread writes nothing)
            A.error(log, "dashboard %s (take-back stash)" % action,
                    {"sid": sid, "uid": uid, "flagged": flagged,
                     "noted": noted})
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
        time.sleep(QUEUE_VERIFY_GAP_S)
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
