# plugins/claude_code/hostctl.py — Claude Code's HostControl adapter.
#
# The host tool's control surface (plugins.host.HostControl). Claude Code drives
# EVERY gesture, so it overrides every one and its derived caps read all-True —
# which is precisely what keeps the dashboard's _caps_guard a no-op for a Claude
# session (the guard never fires when the cap is True).
#
# Named `hostctl`, not `host`, ON PURPOSE: the `host` PROVIDER function in
# plugins/claude_code/__init__.py would shadow a submodule named `host` (a
# package attribute defined in __init__ wins over a same-named submodule for
# `from plugins.claude_code import host`), so the module carries a distinct name.
#
# ROUTING (P2): these bodies ARE the control plane. Every gesture the dashboard
# offers — interrupt / send / rename / autoname / rewind / rewind_to / migrate /
# compact / model / effort / ask / plan — was an inline body in
# dashboard/http/post/*.py and MOVED here byte-identically; the handlers kept
# only their guards (auth, caps, live window, tab state) and the HTTP mapping,
# and `_gesture_host` now hands back a HostControl for EVERY host rather than
# None for the default one. The five Claude SCREEN DRIVERS moved with them
# (askdialog / plandialog / rewindmenu / confirmdialog / suggestion, now this
# package's own modules), which is what made the move possible at all: a plugin
# may not import the dashboard, and those drivers are pure Claude-TUI geometry.
#
# A gesture OWNS its `web-*` audit rows. That is a deliberate split from the
# handler-audits-everything discipline the guards keep: the ROW ORDER within a
# gesture is load-bearing (an interrupt writes its main row and THEN its
# `phase: restore` row; the escape-recheck must not be spawned for a turn that
# never stopped), and only the body knows those orderings. The handler still
# audits every REFUSAL it makes on its own (`_caps_guard`, `_resolve_live_window`,
# the dialog/busy 409s) with the same `web-*` kind, so a gesture's row and its
# rejections still land in one place.
#
# Each override is a distinct function object (that identity is what caps()
# reads as "overridden"), so they are written out rather than generated in a
# loop, which would share ONE object and read as not-overridden.
import os
import re
import time
from functools import partial

from core import clipimg
from core import paths as P
from core import sessionapi as API
from core import spawn as SP
from core import tabs
from core.noaudit import load_audit
from core.render import strip_ansi
from plugins.host import INDETERMINATE, HostControl

A = load_audit()

# --- interrupt verification tuning ------------------------------------------
# A single synthesized Escape via `kitten @ send-key` is only ~2/3 reliable
# (kitty reports no per-window delivery), so a blind press silently misses — a
# fresh web-launched turn ran to completion despite ok:true (2026-07-24, session
# a16a181f). A BUSY-tab interrupt is VERIFIED by screen delta and re-pressed
# WHILE the turn is still live — but never on an idle box (a stray Esc there
# could open /rewind). INTERRUPT_RETRY_S sits well above the TUI's own ~150 ms
# double-Esc detection window, so two spaced retries never read as a double-Esc.
#
# The knobs live with their one reader (the gesture), as they did beside the
# handler that used to hold this body; a test patches this module.
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

QUEUE_VERIFY_GAP_S = 0.5           # gap between the TWO screen captures whose
#                                    equality decides "is a turn REALLY running"
#                                    before a send promises `queued`. The tab
#                                    colour alone cannot promise it: Claude Code
#                                    fires NO hook on cancel, so a turn cancelled
#                                    AT THE TERMINAL (Esc-Esc) leaves the tab
#                                    frozen on magenta and the send reports
#                                    `queued` for a message the idle TUI submits
#                                    instantly — pinning a ⧗ chip no prompt will
#                                    ever drain (session bdeca061, 2026-07-25).
#                                    Same marker-free screen-DELTA liveness as
#                                    the interrupt's verify, and the same value
#                                    for the same reason. Paid only on a queueing
#                                    send, where the message is queueing anyway.

RESTORE_MATCH_CHARS = 40           # prefix length compared when deciding
#                                    whether the input box now holds the message
#                                    the just-pressed Escape took back
#                                    (_restored_input, over suggestion.cmp_key —
#                                    whitespace REMOVED, so the box's wrap points
#                                    can't matter). A prefix, not the whole
#                                    string, so a box that clipped a long tail
#                                    still matches; long enough that two real
#                                    prompts don't collide.

# The ✦ / ✧ menu vocabularies — Claude Code's own model aliases and reasoning
# levels, and the SINGLE owner of both (the dashboard's two client tables read
# them off the wire now). Aliases rather than concrete ids: the CLI resolves
# `opus` to whatever opus it currently ships, which is also why a running
# session's id matches a row by FAMILY (model_match below). The first model is
# the new-session form's first-ever default (model_default).
MODEL_CHOICES = ("fable", "opus", "sonnet", "haiku")
EFFORT_CHOICES = ("low", "medium", "high", "xhigh", "max")

# A Claude Code thinking-spinner gerund (a spinner glyph, then a word, then the
# `…` ellipsis — e.g. `✻ Sock-hopping…`). DIAGNOSTIC ONLY: it labels an
# `interrupt-probe` capture's phase; the interrupt's liveness decision is
# screen-delta, so this pattern's version-fragility is harmless.
_SPIN_RE = re.compile(r"[^\s\w]\s+\w[\w-]*…")


class ClaudeCodeHost(HostControl):
    name = "claude_code"
    label = "Claude Code"
    launchable = True

    # Claude Code's TUI auto-attaches whatever IMAGE the clipboard holds to a
    # message on ANY bracketed paste (and on an argv-prompt startup) — proven
    # live: a web send with a screenshot on the board arrived as "text[Image #1]"
    # with the PNG, though baqylau attached nothing. There is no opt-out, so
    # every paste into this host empties an image clipboard first. Declared
    # rather than assumed: codex does NOT do this and must not pay the osascript
    # (docs/dashboard.md *Clipboard-image guard*).
    paste_grabs_clipboard_image = True

    # --- CONTROL gestures -----------------------------------------------------

    def interrupt(self, fe, win, ctx):
        """Press Escape until the turn is verifiably dead, then read back what
        the TUI handed to its input box. THE stop gesture (docs/dashboard.md,
        *Interrupt*): it stops the current turn in place, the session stays up.

        ROBUST verified interrupt. A single synthesized Escape does NOT reliably
        stop a busy turn here: kitty reports no per-window delivery (~2/3
        reliable), AND with vim editorMode (the user's `editorMode: vim`) the
        FIRST Escape during the thinking phase only leaves INSERT mode — it never
        reaches the interrupt handler, so the turn runs to completion (measured
        2026-07-24: every real single-Esc interrupt on a `thinking` tab missed —
        a16a181f / 3d70feca — while a mid-STREAM Esc landed; the throwaway diff
        showed the lone Esc deleting `-- INSERT --` and nothing else). So press
        Escape, then WHILE the turn is still LIVE, press again. Liveness is NOT a
        marker string (spinner glyphs animate, gerunds vary, thinking levels
        differ) — it is whether the screen is still CHANGING between two captures
        INTERRUPT_RETRY_S apart: a running turn always ticks its spinner/
        elapsed-timer/stream, a stopped one is static. Stop the instant it goes
        static, so an idle box never gets a stray Esc. Every capture is folded
        into an `interrupt-probe` audit row — the ground truth for any recurrence.

        `ctx`: sid/log/sdb/tab/action (the `web-*` row kind) + `verb` (the
        A.error phrase), `row` (the audit session row — its transcript path and
        size are the escape-recheck's growth baseline, READ BEFORE the key
        lands), `queueing` (is this tab one where a send would queue — the
        dashboard's own QUEUE_TABS policy), `box` (the web's input-box stash) and
        `last_prompt` (a THUNK whose `.rec` is (text, uid) of the last prompt
        record — the read model's answer to WHAT a restore would be restoring,
        LAZY because resolving it parses the whole transcript and the ctx is
        built before the key is sent; see dashboard/read/session.LastPrompt).

        Result {status, cid, ok, stopped, queued, restored}: `stopped` True =
        verified static (dead), False = still animating after every re-press (the
        Esc never landed — the caller 502s and NO recheck is spawned, because
        flipping the tab green would MASK a turn that is still running), None =
        idle press / unreadable."""
        log, sdb = ctx.get("log") or "", ctx.get("sdb") or ""
        action = ctx.get("action") or "web-interrupt"
        verb = ctx.get("verb") or "interrupt"
        tab = ctx.get("tab") or ""
        r = self._ack()
        tpath, tsize = _press_baseline(ctx.get("row") or {})
        pre = self._screen(fe, win)
        ok = bool(fe.send_key(win, "escape"))
        attempts, stopped, drained = 1, None, ""
        probes = [self._phase(pre, "pre-esc")]
        if ok and ctx.get("queueing"):
            from plugins.claude_code import transcript
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
        r["ok"], r["stopped"] = ok, stopped
        r["queued"], r["restored"] = bool(drained), ""
        if not ok:
            A.error(log, "dashboard %s (send failed)" % verb,
                    {"sid": ctx.get("sid"), "win": win})
            r["status"] = INDETERMINATE
            return r
        if stopped is False:
            # The screen kept animating after every re-press — the turn never
            # stopped. Do NOT spawn the escape-recheck: flipping the tab green
            # would MASK a turn that is still running (exactly how the failure
            # hid). Surface it so the page toasts a failure, not a phantom
            # success — and the `interrupt-probe` row says which phase it was.
            A.error(log, "dashboard %s (not stopped)" % verb,
                    {"sid": ctx.get("sid"), "win": win, "attempts": attempts})
            r["status"] = INDETERMINATE
            return r
        if tab in (tabs.THINKING, tabs.WORKING):
            # An Esc killed mid-think leaves NO signal anywhere (the known
            # interrupt-watch gap) — but a WEB interrupt is itself an event,
            # so spawn the escape-recheck: flip the dead magenta green unless
            # any real signal (state movement / transcript growth) shows up
            # within its grace. Detached + audited (A.spawn); its verdict
            # lands as tab_transitions rows under DISPATCH escape-recheck.
            self._spawn_escape_recheck(fe, win, log, tpath, tsize)
        r["restored"] = self._restored_input(fe, win, ctx)
        return r

    def send(self, fe, win, text, ctx):
        """Deliver `text` into the session's input box as a user message.

        `clear_draft` (ctx): the TUI input already holds text the web put there —
        an interrupt that took the last message back, or a rewind that restored
        one — so the send first kills that draft (tui.clear_input: Ctrl+U to
        start + Ctrl+K to end per line, a backspace between lines) and then
        delivers the text as a BRACKETED PASTE: a raw send into the just-cleared
        input drops leading bytes (measured — the mangle), an atomic paste
        doesn't. This is what lets you edit AND resend from the web without
        touching the kitty tab.

        ALWAYS a bracketed paste, not a raw send: a raw send is delivered as fast
        individual keystrokes and the TUI drops some depending on its input state
        (reported live: "test" arrived as "t"; measured 8/8 clean for a bracketed
        paste, flaky for raw). The trailing CR is a separate keystroke OUTSIDE
        the paste, so it still submits — and a multi-line composer message pastes
        atomically instead of its internal newlines submitting it early.

        `ctx`: sid/log/sdb/tab/action, `clear_draft`, `box` (the input-box
        stash — its `.draft` drives the multi-line kill and the send CONSUMES
        it), `live`/`queueing` (the caller's turn-liveness verdict, folded into
        the row), `attachments` (count, for the row). Result {status, cid, ok}."""
        from plugins.claude_code import tui
        log, sdb = ctx.get("log") or "", ctx.get("sdb") or ""
        action = ctx.get("action") or "web-send"
        box = ctx.get("box")
        pending_draft = ctx.get("prev_text") or ""
        clear_draft = bool(ctx.get("clear_draft"))
        r = self._ack()
        draft_lines = 0
        if clear_draft:
            # kill the restored draft, settle, then paste. Ctrl+U/Ctrl+K clear
            # ONE line, and a take-back can hold a MULTI-LINE draft (session
            # 8b9f870b, 2026-07-29) — the stash knows the exact text, so kill one
            # line per newline. The cursor sits on the LAST line after a restore;
            # a body-flag-only clear (no stash) keeps the single-line kill.
            draft_lines = tui.clear_input(fe, win, pending_draft)
        # empty an IMAGE clipboard first — a bracketed paste makes Claude Code
        # attach whatever image is on the board (paste_grabs_clipboard_image,
        # docs/dashboard.md *Clipboard-image guard*); no-op on a text clipboard
        # / off macOS.
        clip = clipimg.clear_image()
        if box is not None:
            box.note_send()    # our paste is about to sit in the box for a
            #                    beat — the draft sync must not read it back
        ok = bool(fe.paste_text(win, text))
        A.state_file(log, sdb, action,
                     {"win": win, "chars": len(text), "ok": ok,
                      "tab": ctx.get("tab") or "",
                      "clear_draft": clear_draft,
                      "tui_draft": bool(pending_draft),
                      "draft_lines": draft_lines,
                      "attachments": int(ctx.get("attachments") or 0),
                      "clip": clip, "live": ctx.get("live"),
                      "queued": bool(ctx.get("queued"))})
        r["ok"] = ok
        if not ok:
            A.error(log, "dashboard message (send failed)",
                    {"sid": ctx.get("sid"), "win": win})
            r["status"] = INDETERMINATE
            return r
        if pending_draft and box is not None and not box.set_draft(""):
            # a stale flag only costs an extra Ctrl+U/K on an empty line, but
            # it means the STASH is broken — the same write path the take-back
            # depends on, so surface it
            A.error(log, "dashboard message (tui-draft clear)",
                    {"sid": ctx.get("sid"), "win": win})
        return r

    def rename(self, sid, name, ctx):
        """Rename a LIVE session: paste Claude Code's own `/rename <name>`
        through the one slash-command channel (tui.type_command — mode-proof
        against `editorMode: vim`, clipboard-image guarded). Claude Code then
        updates its in-memory title, writes the `agent-name` record itself and
        re-emits the OSC the kitty tab follows, so all four readers agree from
        ONE write.

        Mid-turn it lands in the TUI's message queue and applies at the turn
        boundary (`queued`, exactly like the ✦ auto button and the other quick
        commands).

        No `Frontend.set_tab_title` here on purpose: a sticky tab title would be
        a SECOND writer of the name, free to disagree with the one the session
        actually has (a queued rename the user then Escapes out of would leave
        the tab asserting a name nothing else knows) — which is the exact split
        the bug this replaced presented as.

        fe/win ride in `ctx` because the gesture signature is sid-keyed (a parked
        session has no window; the caller takes the PARKED path for it, which is
        a transcript write, not a gesture). Result {status, cid, ok}."""
        from plugins.claude_code import tui
        fe, win = ctx.get("fe"), ctx.get("win")
        log, sdb = ctx.get("log") or "", ctx.get("sdb") or ""
        if not (fe and win):
            return self._rejected()
        ok, clip = tui.type_command(fe, win, "/rename " + name)
        queued = bool(ctx.get("queueing"))
        A.state_file(log, sdb, ctx.get("action") or "web-rename",
                     {"win": win, "chars": len(name), "ok": ok,
                      "tab": ctx.get("tab") or "", "channel": "tui",
                      "queued": queued, "clip": clip})
        r = self._ack()
        r["ok"] = ok
        if not ok:
            A.error(log, "dashboard rename (send failed)",
                    {"sid": sid, "win": win})
            r["status"] = INDETERMINATE
        return r

    def autoname(self, fe, win, ctx):
        """Let Claude Code NAME THE SESSION ITSELF — the argless `/rename`, the
        web's ✦ auto button. A named rename never comes through here (that is the
        `rename` gesture, whose transcript append works parked too); this is the
        one command whose whole point is that the TUI generates the title.

        Signature is (fe, win, ctx) like the other typing gestures; it shares the
        `rename` CAP rather than owning one, since a host that can be told a name
        and one that can invent it are the same capability from the button's
        point of view. Result {status, cid, ok}."""
        from plugins.claude_code import tui
        ctx = ctx or {}
        log, sdb = ctx.get("log") or "", ctx.get("sdb") or ""
        ok, clip = tui.type_command(fe, win, "/rename")
        A.state_file(log, sdb, ctx.get("action") or "web-command",
                     {"win": win, "cmd": "rename", "arg": "", "ok": ok,
                      "tab": ctx.get("tab") or "", "clip": clip})
        r = self._ack()
        r["ok"] = ok
        if not ok:
            A.error(log, "dashboard command (send failed)",
                    {"sid": ctx.get("sid"), "win": win, "cmd": "rename"})
            r["status"] = INDETERMINATE
        return r

    def rewind(self, fe, win, ctx):
        """OPEN Claude Code's rewind/checkpoint menu by TYPING `/rewind`
        (documented identical to the idle double-Esc; synthesized double-press
        key events opened the menu only ~2/3 at the best gap while the typed
        command opened it every time). No Escape pressed ⇒ no recheck. The menu
        is then the TERMINAL user's to drive; `rewind_to` is the web's own
        end-to-end restore. Result {status, cid, ok}."""
        from plugins.claude_code import tui
        log, sdb = ctx.get("log") or "", ctx.get("sdb") or ""
        ok, clip = tui.type_command(fe, win, "/rewind")
        A.state_file(log, sdb, ctx.get("action") or "web-rewind",
                     {"win": win, "ok": ok, "tab": ctx.get("tab") or "",
                      "clip": clip})
        r = self._ack()
        r["ok"] = ok
        if not ok:
            A.error(log, "dashboard rewind (send failed)",
                    {"sid": ctx.get("sid"), "win": win})
            r["status"] = INDETERMINATE
        return r

    def rewind_to(self, fe, win, target, mode, ctx):
        """FULL web rewind — restore the session to the checkpoint of a SPECIFIC
        prompt without touching the kitty tab (docs/dashboard.md, *Web rewind*):
        drives Claude Code's own rewind menu via rewindmenu.drive (typed
        `/rewind`, screen-verified navigation, digit resolved from the parsed
        option labels).

        `target` is the prompt's full text (menu entries are its first line,
        truncation-aware), `mode` one of rewind_modes(), `ctx['ups']` the
        target's `up`-press distance from the menu's "(current)" cursor start — a
        jump hint the text-verify scan corrects.

        A step that didn't verify is a MenuError: the menus are already closed
        (this driver's bail IS Escape, unlike ask/plan), and the result carries
        `step` + the clipped screen it gave up on. On success the response's
        `restored` echoes `target` for conversation restores — Claude Code puts
        the rewound prompt back into the TUI input, so the box now holds it and
        the next send must REPLACE rather than append (the `box` stash, the same
        flag the interrupt's take-back sets). Result {status, cid, ok, mode,
        restored, degraded, step?, detail?}."""
        from core.screendrive import clip_screen
        from plugins.claude_code import rewindmenu
        log, sdb = ctx.get("log") or "", ctx.get("sdb") or ""
        action = ctx.get("action") or "web-rewind-to"
        tab = ctx.get("tab") or ""
        ups = int(ctx.get("ups") or 0)
        r = self._ack()
        try:
            res = rewindmenu.drive(fe, win, target, mode, ups=ups)
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
                    {"sid": ctx.get("sid"), "win": win, "mode": mode,
                     "detail": str(e), "screen": seen})
            A.state_file(log, sdb, action,
                         {"win": win, "ok": False, "tab": tab, "mode": mode,
                          "ups": ups, "step": e.step})
            return {"status": INDETERMINATE, "cid": r["cid"], "ok": False,
                    "step": e.step, "detail": str(e)}
        A.state_file(log, sdb, action,
                     {"win": win, "ok": True, "tab": tab, "mode": mode,
                      "ups": ups, "steps": res["steps"],
                      "digit": res["digit"], "degraded": res["degraded"]})
        restored = target if mode in ("conversation", "both") else ""
        box = ctx.get("box")
        if restored and box is not None and not box.set_draft(restored):
            # Claude Code puts the rewound-to prompt back in the input box, so
            # the next send must REPLACE it, not paste after it (the tui-draft
            # stash — the same server-owned flag the interrupt's take-back sets)
            A.error(log, "dashboard rewind-to (tui-draft stash)",
                    {"sid": ctx.get("sid"), "win": win})
        r["ok"], r["mode"] = True, mode
        r["restored"], r["degraded"] = restored, res["degraded"]
        return r

    def migrate(self, sid, ctx):
        """Hand the session to another subscription account — the header's ⇆
        migrate button (docs/relimit.md *Manual migrate*). Spawns the SAME
        detached migrator the automatic rate-limit path uses
        (bin/claude-relimit.py: close the tab → wait for the SessionEnd park →
        `<alias> claude --resume <sid>` in a new tab; the adopt machinery carries
        the mirror history and the status-line capture flips the account chip),
        with two manual-intent differences baked into `mode=manual`: no
        auto-continue nudge (nothing was cut off — the resumed session opens at
        the prompt) and no 90% usage ceiling on the target
        (account.pick_target(manual=True) — an explicit click outranks the refuge
        rule). It runs the SAME fable→opus→sonnet downgrade ladder the automatic
        path does (docs/relimit.md *Model-downgrade ladder*): same model on
        another account when one has quota, else a downgrade rung passed through
        to `--model` (the current model is read off the transcript).

        Works live AND parked: a parked session skips the close leg and just
        relaunches. Result {status, cid, ok, target}: `target` None means no
        account (any rung) qualifies — a 409 for the caller, with the picker's
        FULL reasoning already audited so the refusal is reconstructible from the
        DB (the same subtle gap the automatic path closed with `relimit-pick`; a
        bare "no target" is undebuggable)."""
        from plugins.claude_code import account, transcript
        log, sdb = ctx.get("log") or "", ctx.get("sdb") or ""
        row = ctx.get("row") or {}
        r = self._ack()
        cur = (API.kv_at(sdb, "account") or {}).get("slug") or ""
        # The model the session is running (off its transcript) feeds the
        # downgrade ladder: a manual ⇆ now downgrades too when no account has
        # the current model free.
        cur_model = (transcript.context_probe(row.get("transcript_path") or "")
                     or {}).get("model") or ""
        # ceiling=None IS the manual relaxation (an explicit click outranks the
        # 90% refuge rule the automatic path keeps) — the same call
        # plugins.migration_target(manual=True) composes, made directly here
        # because this host owns both sides of it.
        pick = {}
        target = account.pick_target(cur, cur_model, ceiling=None, explain=pick)
        if target is None:
            A.state_file(log, sdb, ctx.get("action") or "web-migrate",
                         {"ok": False, "reason": "no target", "from": cur,
                          "pick": pick})
            r["ok"], r["target"] = False, None
            return r
        # target["model"] is the downgrade rung (or "" for a same-model migrate);
        # pick_target already resolved same-vs-downgrade, so forward it verbatim.
        proc = SP.spawn_detached(
            os.path.join(P.BIN, "claude-relimit.py"),
            [log, sid, target["slug"], target["alias"],
             row.get("cwd") or "", "manual", target["model"]],
            log, purpose="relimit:%s (web)" % target["slug"])
        ok = proc is not None
        A.state_file(log, sdb, ctx.get("action") or "web-migrate",
                     {"ok": ok, "from": cur, "to": target["slug"],
                      "model": target["model"], "eff": target["eff"],
                      "cwd": row.get("cwd") or "", "pick": pick})
        r["ok"], r["target"] = ok, target
        if not ok:                       # spawn failure already audited by SP
            r["status"] = INDETERMINATE
        return r

    def compact(self, fe, win, ctx):
        """Compact (summarise) the conversation — the TUI's own `/compact`,
        pasted through the slash-command channel. Result {status, cid, ok}."""
        return self._command(fe, win, ctx, "compact", "", "/compact")

    def model(self, fe, win, arg, ctx):
        """Switch the session's model — `/model <arg>`, then auto-answer the
        switch-confirm menu newer TUI builds interpose (see _confirm)."""
        return self._command(fe, win, ctx, "model", arg, "/model " + arg)

    def effort(self, fe, win, arg, ctx):
        """Switch the session's reasoning effort — `/effort <arg>`, with the same
        confirm-menu auto-Yes as `model`."""
        return self._command(fe, win, ctx, "effort", arg, "/effort " + arg)

    def _command(self, fe, win, ctx, cmd, arg, text):
        """Shared body of compact/model/effort: the one slash-command channel (a
        bracketed paste — a raw typed command is vim KEYSTROKES in a NORMAL-mode
        box) plus, for model/effort on a settled tab, the confirm-menu auto-Yes.
        Writes the canonical `web-command` row. Result {status, cid, ok,
        confirm?}."""
        from plugins.claude_code import tui
        log, sdb = ctx.get("log") or "", ctx.get("sdb") or ""
        tab = ctx.get("tab") or ""
        ok, clip = tui.type_command(fe, win, text)
        A.state_file(log, sdb, ctx.get("action") or "web-command",
                     {"win": win, "cmd": cmd, "arg": arg or "", "ok": ok,
                      "tab": tab, "clip": clip})
        r = self._ack()
        r["ok"] = ok
        if not ok:
            A.error(log, "dashboard command (send failed)",
                    {"sid": ctx.get("sid"), "win": win, "cmd": cmd})
            r["status"] = INDETERMINATE
            return r
        if cmd in ("model", "effort") and not ctx.get("queueing"):
            r["confirm"] = self._confirm(fe, win, ctx, cmd)
        return r

    def _confirm(self, fe, win, ctx, cmd):
        """Answer the model/effort switch-confirm menu. Newer TUI builds
        interpose a Yes/No menu (the prompt-cache warning) instead of applying
        outright — unanswered it makes the click look dead, so press its own Yes
        (the button IS the consent), screen-verified: confirmdialog.py. Mid-turn
        (queued) the command only runs at the turn boundary, so there is no menu
        to wait for and this is not called — an unanswered late menu surfaces as
        the red-tab notification."""
        from plugins.claude_code import confirmdialog
        log, sdb = ctx.get("log") or "", ctx.get("sdb") or ""
        try:
            c = confirmdialog.confirm(fe, win)
            verdict = "confirmed" if c["dialog"] else "none"
        except Exception as e:      # ConfirmError or a frontend hiccup —
            # the menu (if any) is left open for the terminal user
            A.error(log, "dashboard command (confirm failed)",
                    {"sid": ctx.get("sid"), "win": win, "cmd": cmd,
                     "err": str(e)})
            verdict = "failed"
        A.state_file(log, sdb, "web-command-confirm",
                     {"win": win, "cmd": cmd, "confirm": verdict})
        return verdict

    def ask(self, fe, win, answers, ctx):
        """Answer the session's OPEN AskUserQuestion dialog by driving the TUI's
        own dialog with screen-verified key events (askdialog.drive).

        `ctx['chat']` is the dialog's own "Chat about this" — it DECLINES the
        questions and invites discussion, so it carries no answers (the caller
        already checked the word against ask_declines()). A step that didn't
        verify is an AskError: the dialog is left OPEN, never Escape-closed
        (Escape would DECLINE the questions), and `step` says what failed so a
        retry from the card re-normalizes.

        Result {status, cid, ok, step?, detail?, screen?}: `screen` is the
        clipped capture the failing step saw, for the caller's self-heal."""
        from core.screendrive import clip_screen
        from plugins.claude_code import askdialog
        log, sdb = ctx.get("log") or "", ctx.get("sdb") or ""
        action = ctx.get("action") or "web-answer"
        chat = bool(ctx.get("chat"))
        tid = ctx.get("tool_use_id") or ""
        r = self._ack()
        try:
            askdialog.drive(fe, win, ctx.get("questions") or [], answers or [],
                            chat=chat)
        except askdialog.AskError as e:
            c = {"sid": ctx.get("sid"), "win": win, "chat": chat,
                 "detail": str(e)}
            if e.screen is not None:      # the pixels the failing step saw
                c["screen"] = clip_screen(e.screen)
            A.error(log, "dashboard answer (%s)" % e.step, c)
            A.state_file(log, sdb, action,
                         {"win": win, "ok": False, "chat": chat,
                          "step": e.step, "tool_use_id": tid})
            return {"status": INDETERMINATE, "cid": r["cid"], "ok": False,
                    "step": e.step, "detail": str(e)}
        A.state_file(log, sdb, action,
                     {"win": win, "ok": True, "chat": chat,
                      "tool_use_id": tid})
        r["ok"] = True
        return r

    def deliver(self, fe, win, text, ctx):
        """The ask card's FOLLOW-UP message: a PREVIEW-layout question has no
        typed-answer row (askdialog._require_type_row), so the card routes a
        TYPED answer through 'Chat about this' and carries the typed text along.
        Once the dialog is dismissed (drive waited for that) it is delivered as a
        normal message so the user's custom answer reaches the session
        (docs/dashboard.md, *Web ask*).

        A plain paste with the clipboard-image guard — deliberately NOT `send`,
        which owns the draft-clear/liveness/`tui-draft` machinery a freshly-
        dismissed dialog has nothing to do with. Result {status, cid, ok}."""
        log, sdb = ctx.get("log") or "", ctx.get("sdb") or ""
        clip = clipimg.clear_image()         # clipboard-image guard, as send
        sent = bool(fe.paste_text(win, text))
        A.state_file(log, sdb, "web-send",
                     {"win": win, "chars": len(text), "ok": sent,
                      "via": ctx.get("via") or "", "clip": clip})
        if not sent:
            A.error(log, "dashboard answer-chat message (send failed)",
                    {"sid": ctx.get("sid"), "win": win})
        r = self._ack()
        r["ok"] = sent
        if not sent:
            r["status"] = INDETERMINATE
        return r

    def plan_options(self, fe, win, ctx):
        """The plan card's decision options, read off the LIVE SCREEN: the
        dialog's labels VARY with the session's permission mode ("Yes, and bypass
        permissions" vs "Yes, and auto-accept edits"), so they cannot be a
        table. Read-only — no key is pressed. Result {status, cid, ok, options,
        step?, detail?}; a bail's `step` lets the caller self-heal a stash whose
        dialog resolved in the terminal."""
        from plugins.claude_code import plandialog
        r = self._ack()
        try:
            r["options"] = plandialog.options(fe, win)
        except plandialog.PlanError as e:
            return {"status": INDETERMINATE, "cid": r["cid"], "ok": False,
                    "step": e.step, "detail": str(e)}
        r["ok"] = True
        return r

    def plan(self, fe, win, decision, ctx):
        """Decide the session's OPEN ExitPlanMode dialog (docs/dashboard.md, *Web
        plan mode*) by driving the TUI's own dialog. `decision` is one of the
        shapes plan_decisions() names:
          · {"dismiss": True}        — Escape, the TUI's own reject-and-keep-planning
          · {"feedback": text}       — the "Tell Claude what to change" row:
                                       focus, type, Enter (rejects with feedback;
                                       newlines collapse — single-line editor)
          · {"digit": D, "label": L} — press that decision row, verified against
                                       the live screen (label drift = 409,
                                       nothing pressed)

        An unverified step is a PlanError: the dialog is left OPEN (an Escape
        bail would REJECT a plan the user may still approve), and an `open` bail
        lets the caller self-heal the stash. Result {status, cid, ok, kind,
        step?, detail?}."""
        from plugins.claude_code import plandialog
        log, sdb = ctx.get("log") or "", ctx.get("sdb") or ""
        action = ctx.get("action") or "web-plan"
        tid = ctx.get("tool_use_id") or ""
        decision = decision or {}
        label = decision.get("label") or ""
        r = self._ack()
        # one driver call per body shape, bound to a zero-arg callable so the
        # single try/except below owns the PlanError handling for all three
        if decision.get("dismiss"):
            kind, run = "dismiss", partial(plandialog.dismiss, fe, win)
        elif decision.get("feedback"):
            kind = "feedback"
            run = partial(plandialog.feedback, fe, win, decision["feedback"])
        else:
            kind = "decide"
            run = partial(plandialog.decide, fe, win,
                          str(decision.get("digit")), label)
        r["kind"] = kind
        try:
            run()
        except plandialog.PlanError as e:
            A.error(log, "dashboard plan (%s)" % e.step,
                    {"sid": ctx.get("sid"), "win": win, "kind": kind,
                     "detail": str(e)})
            A.state_file(log, sdb, action,
                         {"win": win, "ok": False, "kind": kind,
                          "step": e.step, "tool_use_id": tid})
            return {"status": INDETERMINATE, "cid": r["cid"], "ok": False,
                    "kind": kind, "step": e.step, "detail": str(e)}
        A.state_file(log, sdb, action,
                     {"win": win, "ok": True, "kind": kind,
                      "label": label, "tool_use_id": tid})
        r["ok"] = True
        return r

    # --- per-host VOCABULARY the control plane validates against --------------

    def mention(self, path):
        """`@path` — Claude Code's TUI-native file mention, the ONE native image
        path (the CLI has no image flag). The TUI resolves it and attaches the
        file, so an uploaded attachment rides the existing paste/launch-argv
        transport as text."""
        return "@" + path

    def clear_input(self, fe, win, prev_text=""):
        """Kill whatever is in the `❯` box (tui.clear_input — Ctrl+U/Ctrl+K per
        line, a backspace between lines), so a paste REPLACES rather than
        appends. Returns the number of lines killed."""
        from plugins.claude_code import tui
        return tui.clear_input(fe, win, prev_text)

    def turn_live(self, fe, win, ctx=None):
        """Is a turn ACTUALLY running in `win`? True = yes, False = the box is
        static (idle), None = unreadable. Two ANSI-stripped captures
        QUEUE_VERIFY_GAP_S apart — the same marker-free screen-DELTA liveness the
        interrupt's verify uses (a running turn always ticks its spinner /
        elapsed timer / token stream; a stopped one is static), because no marker
        string survives: glyphs animate, gerunds vary, thinking and streaming
        phases differ.

        The tab colour is NOT a substitute: Claude Code fires no hook on cancel,
        so a terminal-side Esc-Esc leaves it frozen mid-turn. The send calls this
        before promising `queued`; deliberately NOT folded together with the
        interrupt's own loop, which re-presses between captures."""
        a = self._screen(fe, win, why="send")
        time.sleep(QUEUE_VERIFY_GAP_S)
        b = self._screen(fe, win, why="send")
        if a is None or b is None:
            return None
        return a != b

    def ask_declines(self):
        """The words that DECLINE this host's question dialog instead of
        answering it: Claude Code's "Chat about this" (declines + invites
        discussion; the page then focuses its composer). A word outside this
        vocabulary is a 409 naming it — codex's dialog has no decline row at
        all, and silently dropping the flag would answer a question the user
        meant to dodge."""
        return ("chat",)

    def plan_decisions(self):
        """The decision shapes this host's plan dialog accepts, in the order the
        caller's "need one of …" message names them: a numbered row
        (`digit`+`label`), the free-text "Tell Claude what to change"
        (`feedback`), and Escape (`dismiss`). codex's picker has no feedback row,
        which is why this is a per-host vocabulary rather than a body-shape
        if/elif."""
        return ("decide", "feedback", "dismiss")

    def rewind_modes(self):
        """The restore modes this host's rewind menu offers — the KEYS of
        rewindmenu.MODE_LABELS, its single owner (that table maps each mode to
        the menu label matched on screen, so a menu lacking one is a clean bail
        rather than a wrong digit)."""
        from plugins.claude_code import rewindmenu
        return tuple(rewindmenu.MODE_LABELS)

    def rewind_mode_label(self, mode):
        """One rewind mode's menu row text, from the SAME rewindmenu.MODE_LABELS
        table the on-screen match uses — so the web menu and the TUI menu cannot
        word the same restore differently."""
        from plugins.claude_code import rewindmenu
        return rewindmenu.MODE_LABELS.get(mode, "")

    def command_floor(self, cmd):
        """Claude Code's two measured quick-command refusals, as floors on the
        number of YOUR prompts in the conversation:

          compact — `/compact` bounces with "Not enough messages to compact" on a
            barely-started chat. 2 is deliberately the LOWEST floor that catches
            the reported case (you sent one message and it bounced); past it the
            TUI stays the authority, since its exact rule is unpublished.
          rename  — the ARGLESS `/rename` bounces with "Could not generate a name:
            no conversation context yet" on an EMPTY conversation (v2.1.220), so
            it needs 1.

        Both were client-side constants applied to every host."""
        return {"compact": 2, "rename": 1}.get(cmd, 0)

    def title_key(self, tpath):
        """The durable rename-override key for a transcript — the `.jsonl` STEM
        of a Claude session transcript, "" for anything else. Both sides of the
        override (the parked rename's write and the read model's lookup) derive
        it here, so the filename convention is the OWNING host's fact and not a
        `.jsonl` literal in the dashboard tier."""
        base = os.path.basename(tpath or "")
        return base[:-len(".jsonl")] if base.endswith(".jsonl") else ""

    # --- screen READS the web mirrors (no keys pressed) -----------------------

    def input_box(self, fe, win, ctx=None):
        """A LIVE session's input box, read straight off the TUI screen (no hook
        fires for either half): (ghost, typed) — the faint pre-filled "suggested
        answer" Claude Code shows when a turn settles (docs/dashboard.md, *Web
        ghost suggestion*), and the REAL text the user has typed there (*Terminal
        draft sync*). At most one is non-None.

        Behind a host method because the faint-SGR geometry is THIS TUI's: a
        codex host's screen returns garbage through this scrape (verified), and
        the inert base returning (None, None) is what makes "no probe for another
        tool" the default rather than a name check in the read model."""
        from plugins.claude_code import suggestion
        return suggestion.probe_box(fe, win, (ctx or {}).get("sid") or "")

    def ask_region(self, fe, win):
        """The AskUserQuestion dialog pane's text on `win`, or "" when no such
        dialog is on screen. `askdialog.region` isolates the dialog (from its
        header-chip bar down) so a live-ticking status line below it doesn't
        register as change — the notifier DIFFS this to tell "you are answering
        at the terminal" from "the question is sitting there unread", the one
        trace terminal answering leaves (it moves neither the tab nor the
        transcript)."""
        from plugins.claude_code import askdialog
        return askdialog.region(fe.get_text(win) or "")

    def typed_input(self, fe, win):
        """The REAL (non-faint) text the user has typed into `win`'s input box,
        or None. The 'done'-arm analog of ask_region: a green tab you are
        replying to AT THE TERMINAL leaves no other trace (typing into the `❯`
        box moves neither the tab off green nor the transcript until you submit).
        Needs the ANSI capture (faint-SGR detection), unlike ask_region."""
        from plugins.claude_code import suggestion
        return suggestion.typed(fe.get_text(win, ansi=True) or "")

    # --- gesture internals ----------------------------------------------------

    def _restored_input(self, fe, win, ctx):
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
        suggestion. The screen says WHETHER; the TRANSCRIPT says WHAT: a box that
        now holds the message we just sent is a restore, and the exact text
        (newlines and all) comes from the transcript record (ctx['last_prompt'],
        which the caller's read model resolved), because typed()
        whitespace-normalizes a wrapped box. Anything else in the box is the
        user's OWN terminal draft — left alone, never echoed into the composer.

        Matching is on a RESTORE_MATCH_CHARS prefix of suggestion.cmp_key —
        whitespace REMOVED, not just normalized. A message wider than the box (or
        one with its own newlines) is captured as several lines that join without
        a separator, so the words agree but the spaces never do; and the prefix,
        rather than the whole string, keeps a box that clipped the tail from
        reading as a mismatch. A miss just yields "" — the interrupt still
        succeeded, the page simply doesn't prefill."""
        from plugins.claude_code import suggestion, transcript
        log, sdb = ctx.get("log") or "", ctx.get("sdb") or ""
        action = ctx.get("action") or "web-interrupt"
        sid = ctx.get("sid") or ""
        # …and asking for it HERE is the point: `last_prompt` is a thunk, and
        # this is the one path that needs the parse behind it (the caller built
        # the ctx before the Escape was even sent).
        lp = ctx.get("last_prompt")
        last, uid = (getattr(lp, "rec", None) or ("", ""))
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
        # after it (the tui-draft stash; the `testingtesting2` bug)
        box_stash = ctx.get("box")
        noted = bool(hit) and box_stash is not None and box_stash.set_draft(last)
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
        The unit of every screen-DELTA liveness check (the interrupt's verify and
        the send's queued-verify) and of the interrupt's audit probe. `why` names
        the caller in the swallow row (`dashboard <why> (probe)`). Never raises
        (audit-before-swallow)."""
        try:
            raw = fe.get_text(win)
        except Exception:
            A.error("", "dashboard %s (probe)" % why, {"win": win})
            return None
        return strip_ansi(raw) if raw else None

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

    # --- model VOCABULARY / launch plumbing (NOT capability-gated) ------------

    def model_choices(self):
        """The models Claude Code's ✦ menu and the new-session form offer —
        FAMILY aliases (the CLI resolves each to a concrete id), which is why
        `model_match` below is "family". Moved here from the two client tables
        that spelled them (app.10-control.js MODEL_CHOICES and
        app.09-newsession.js TOOL_MODELS.claude_code), unchanged."""
        return list(MODEL_CHOICES)

    def effort_choices(self):
        """The reasoning-effort levels Claude Code accepts (`/effort <level>`
        and the launch's `--effort`). Deliberately NOT dashboard.config.EFFORTS,
        which is the UNION over hosts the arg validator allows: `ultra` is
        codex's level, and offering it here would type a word Claude bounces."""
        return list(EFFORT_CHOICES)

    def model_default(self):
        """The new-session form's first-ever model (was TOOL_MODEL_DEF)."""
        return MODEL_CHOICES[0]

    def effort_default(self):
        """The new-session form's first-ever effort (was TOOL_EFFORT_DEF)."""
        return "high"

    # Claude's menu rows are FAMILY words (`opus`) while a running session
    # reports a full id (`claude-opus-4-8` → `opus-4.8`), so the picker's current
    # row is matched on the leading word. codex keeps the base "exact".
    model_match = "family"

    def model_short(self, model_id):
        """Claude's display spelling of a model id ("claude-opus-4-8" →
        "opus-4.8") — model.short_model, the owner. Overridden here so the read
        model can ask the OWNING host instead of importing this plugin: the same
        grammar was being applied to every host's ids, so a codex agent card's
        model went through Claude's `claude-`-stripping version parser."""
        from plugins.claude_code import model
        return model.short_model(model_id)

    def model_default_effort(self, model_id):
        """Claude's model→default-effort table (model.model_default_effort, the
        owner): opus-4-7 → xhigh, the adaptive-reasoning families → high, else
        "". The twin of model_short, and the other half of the read model's old
        `plugins.claude_code.model` reach."""
        from plugins.claude_code import model
        return model.model_default_effort(model_id)

    def resume_words(self, sid):
        """`claude --resume <sid>` — the argv the dashboard's resume-&-send and
        the relimit migrator already compose (plugins.owns_by names claude_code
        as the ONE tool that can pick a conversation up this way)."""
        return ["--resume", sid] if sid else []

    def launch_words(self, opts):
        """The `claude` "$@" tail for a web new-session launch: `--resume`/
        `--continue` and `--model`/`--effort` riding as positional words ahead of
        the prompt (docs/dashboard.md *Resume & send*). This IS the word-builder
        that used to live inline in dashboard.http.post.session.post_new_session —
        moved here byte-identically so both hosts compose their launch through the
        one HostControl seam. `opts` = {resume, cont, model, effort, prompt}; each
        flag is emitted only when its value is set (`cont` is claude-only — codex
        has no --continue)."""
        opts = opts or {}
        resume = opts.get("resume") or ""
        cont = opts.get("cont")
        model = opts.get("model") or ""
        effort = opts.get("effort") or ""
        prompt = opts.get("prompt") or ""
        return ((["--resume", resume] if resume else [])
                + (["--continue"] if cont else [])
                + (["--model", model] if model else [])
                + (["--effort", effort] if effort else [])
                + ([prompt] if prompt.strip() else []))

    def launch_cmd(self, account_alias=""):
        """claude_code's login-shell command word: the account switcher's alias
        (`c1`/`c2`) or the plain `claude` default. `account_alias` is what the
        dashboard already resolved through plugins.account_alias (a registry-
        vetted bareword); this host varies by account, unlike codex."""
        return account_alias or "claude"

    def lifecycle_end(self, sid, log, reason):
        """Nothing to do: Claude Code fires SessionEnd when its tab is closed and
        the normal end-of-session lifecycle (hostpane.host_end → mirror park,
        pane close, audit close) runs on its own — verified empirically
        2026-07-18. Overridden ONLY to say so; a silent inherited default would
        read as "nobody thought about it"."""
        return None


def _press_baseline(row):
    """The escape-recheck's growth baseline as (transcript_path, size): the
    session's transcript and its byte size, -1 when there is no path or it can't
    be stat'd (the recheck then falls back to its own start-time measurement).
    MUST be read BEFORE the key lands, so even the `[Request interrupted by
    user]` line itself counts as growth — that ordering is the whole point of
    taking the baseline here rather than in the watcher."""
    tpath = row.get("transcript_path") or ""
    try:
        return tpath, (os.path.getsize(tpath) if tpath else -1)
    except OSError:
        return tpath, -1


_HOST = None


def get():
    """The process singleton ClaudeCodeHost (the `host` provider's return)."""
    global _HOST
    if _HOST is None:
        _HOST = ClaudeCodeHost()
    return _HOST
