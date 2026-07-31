# plugins/codex/hostctl.py — codex's HostControl adapter.
#
# codex as a first-class HOST tool (plugins.host.HostControl) — what
# plugins.host_of(path) / host_caps("codex") hand back for a codex session, and
# what the dashboard reads to know which control buttons to offer.
#
# Named `hostctl`, not `host`, for the same reason claude_code's is: the `host`
# PROVIDER function in plugins/codex/__init__.py would shadow a `host` submodule.
#
# P5 wires the codex-SUPPORTED control GESTURES over the SCREEN (the app-server
# transport is not up yet, but a whole-gesture method is exactly the seam that
# transport later replaces WITHOUT touching the dashboard): `interrupt` (a single
# Escape — codex's composer is NOT modal like Claude's vim, so no double-Esc —
# verified by the `turn_aborted` RECORD landing in the rollout, since codex fires
# NO Stop hook), `compact` (`/compact`), `rename` (a LIVE `/rename <name>` paste),
# `ask` (drive codex's own `request_user_input` dialog — plugins/codex/dialog.py),
# and `plan` (decide codex's plan-mode DECISION picker — plugins/codex/
# plandialog.py; codex has no plan-approval TOOL, but the on-screen picker IS
# driveable, the same geometry as the /model picker). Overriding these is what
# flips their DERIVED caps True so the dashboard un-greys the buttons; the ones
# left inert (rewind/migrate/model/effort) read False and stay greyed, the HONEST
# answer — codex has no rewind, no migrate, an interactive `/model` PICKER (not a
# `/model <arg>` we can drive blind), and no live `/effort` (effort is a launch-
# time `-c` only). `send` IS overridden (P2) even though nothing gates it: the
# composer is always reachable, but the delivery is host-routed now, and without
# a body the inert base would answer `unsupported` and 409 every codex message.
# Launch/resume plumbing (below) was live since P6 and is NOT gesture-gated.
#
# The gesture bodies use ONLY the frontend (`fe`) + this plugin's own rollout
# parse — never dashboard code (the layering rule forbids a plugin importing the
# dashboard). That is WHY the codex screen driver lives at plugins/codex/dialog.py
# — the whole gesture, screen driver included, sits behind HostControl so the
# dashboard only ever calls host.<gesture> (docs/codex.md *Codex control
# gestures*). As of P2 Claude Code's drivers live in ITS plugin for the same
# reason, and each gesture also writes its OWN `web-*` audit row (the handler's
# `_host_*` shims that used to write them are gone, along with the last inline
# Claude body they were the counterpart of) — same kinds, same fields.
import os
import time

from core.noaudit import load_audit
from plugins.host import ACK, INDETERMINATE, REJECTED, HostControl

A = load_audit()

# interrupt verification (interrupt): a single synthesized Escape via kitty's
# send-key is only ~2/3 reliable per window, so a blind press can miss — a
# bounded wait for the `turn_aborted` record, then one retry press (the task's
# "bounded wait + one retry"). Well under a second per attempt: codex writes
# turn_aborted ~36ms after the Esc lands (measured), so a short poll suffices;
# two attempts ≈ 1.6s worst case, all on total silence.
INTERRUPT_TRIES = 2
INTERRUPT_VERIFY_S = 0.8      # per-attempt wait for turn_aborted to appear
INTERRUPT_POLL_S = 0.1        # rollout re-read beat inside that wait


class CodexHost(HostControl):
    name = "codex"
    label = "Codex"
    launchable = True

    # (No `lead_prose` declaration since P6: a standalone codex run's prose is
    # not painted into the mirror at all — plugins/codex/stream.py returns early
    # in that register — and where a run DOES paint prose it stamps the op
    # `bubbled`, which every web view honours. The host had nothing left to
    # declare; see HostControl's note.)

    # --- CONTROL gestures (the codex-supported subset; the rest stay inert) ---

    def interrupt(self, fe, win, ctx):
        """Stop the current codex turn: a SINGLE Escape (codex's composer is not
        modal — no Claude-vim double-Esc), VERIFIED by the `turn_aborted` record
        appearing in the rollout (codex fires NO Stop hook, no take-back to the
        input box, no escape-recheck — the record is the only signal). A running
        command may outlive the abort (cooperative). A QUEUED message is delivered
        as a NEW turn (STEER): the queued prompt's own records land right after
        the abort, so this reports `steered=True` and the ⧗ chip drains via the
        normal conversation reconciliation — NOT treated as a plain stop.

        `ctx['transcript']` is this session's transcript — for codex its rollout,
        which is the verify source. (The ctx key is HOST-NEUTRAL: it is the
        caller's `sessions.transcript_path` column, and naming it after one
        host's word for the file put a codex noun in a contract every host
        reads.) Result
        {status, cid, ok, verified, steered, tries}: ACK when turn_aborted was
        observed, INDETERMINATE when the Esc landed but no record appeared (audited
        — the codex-interrupt anomaly signature), REJECTED when nothing could be
        pressed."""
        rp = ctx.get("transcript") or ""
        log, sdb = ctx.get("log") or "", ctx.get("sdb") or ""
        verb = ctx.get("verb") or "interrupt"
        r = self._ack()          # borrow its cid; the status is corrected below
        try:
            pos = os.path.getsize(rp) if rp else -1
        except OSError:
            pos = -1
        ok = verified = steered = False
        tries = 0
        for _ in range(INTERRUPT_TRIES):
            tries += 1
            ok = bool(fe.send_key(win, "escape")) or ok
            if not ok:
                break
            verified, steered = self._verify_abort(rp, pos)
            if verified:
                break
        if ok and not verified:
            A.error(log, "codex interrupt (no turn_aborted)",
                    {"sid": ctx.get("sid"), "win": str(win), "tries": tries})
        r["status"] = ACK if verified else (INDETERMINATE if ok else REJECTED)
        r["ok"], r["verified"] = ok, verified
        r["steered"], r["tries"] = steered, tries
        r["queued"], r["restored"] = steered, ""
        A.state_file(log, sdb, ctx.get("action") or "web-interrupt",
                     {"win": win, "ok": ok, "tab": ctx.get("tab") or "",
                      "host": self.name, "status": r["status"], "cid": r["cid"],
                      "verified": verified, "steered": steered, "tries": tries})
        if not ok:
            A.error(log, "dashboard %s (%s send failed)" % (verb, self.name),
                    {"sid": ctx.get("sid"), "win": win})
        return r

    def _verify_abort(self, rp, pos, sleep=time.sleep):
        """Poll the rollout from byte `pos` for a `turn_aborted` RECORD (matched
        through rollout.parse, never a raw byte scan — the invariant), up to
        INTERRUPT_VERIFY_S. Returns (verified, steered): `steered` is True when a
        NEW turn (`task_started`/`prompt`) starts right after the abort — codex's
        queue+Esc, where the delivered message owns the tab. (False, False) when
        no record appears / the rollout is unreadable / `pos` is unknown."""
        from plugins.codex import rollout
        if pos < 0:
            return False, False
        deadline = time.monotonic() + INTERRUPT_VERIFY_S
        while time.monotonic() < deadline:
            sleep(INTERRUPT_POLL_S)
            try:
                size = os.path.getsize(rp)
            except OSError:
                return False, False
            if size <= pos:
                continue
            try:
                with open(rp, "rb") as f:
                    f.seek(pos)
                    chunk = f.read(size - pos)
            except OSError:
                continue
            lines = chunk.split(b"\n")
            abort_idx = -1
            for idx, ln in enumerate(lines[:-1]):    # only COMPLETE lines decidable
                rec = _rec(rollout, ln)
                if rec and rec.get("kind") == "turn_aborted":
                    abort_idx = idx
                    break
            if abort_idx < 0:
                continue
            steered = False
            for ln in lines[abort_idx + 1:]:
                rec = _rec(rollout, ln)
                if rec and rec.get("kind") in ("task_started", "prompt"):
                    steered = True
                    break
            return True, steered
        return False, False

    def send(self, fe, win, text, ctx):
        """Deliver a composer message into the codex window: a PLAIN bracketed
        paste (+ the Enter kitten_send_text rides outside it), and nothing else.

        Everything Claude Code's send does around the paste is deliberately
        ABSENT here, and each absence is a declaration rather than an oversight:
        no clipboard-image wipe (`paste_grabs_clipboard_image` is False — codex's
        TUI does not auto-attach the board, so the ~150ms osascript round-trip
        bought nothing and ran on every message), no Ctrl+U/Ctrl+K line kill
        (`clear_input` is inert — codex's composer is not the same input model
        and blind line-kill keystrokes into it are a guess), and no `tui-draft`
        stash to consume (nothing in this plugin ever puts text in that box).
        `send` is not caps-gated — the composer is always reachable — so this
        override exists to make that reachability HONEST: without a body the
        inert base would answer `unsupported` and 409 every codex message.

        Writes the canonical `web-send` row (the same fields the Claude host
        writes, so one query reads both hosts' sends) plus host/status/cid."""
        log, sdb = ctx.get("log") or "", ctx.get("sdb") or ""
        ok = bool(fe.paste_text(win, text))
        r = self._ack() if ok else self._rejected()
        r["ok"] = ok
        A.state_file(log, sdb, ctx.get("action") or "web-send",
                     {"win": win, "chars": len(text), "ok": ok,
                      "tab": ctx.get("tab") or "",
                      "clear_draft": bool(ctx.get("clear_draft")),
                      "tui_draft": False, "draft_lines": 0,
                      "attachments": int(ctx.get("attachments") or 0),
                      "clip": False, "live": ctx.get("live"),
                      "queued": bool(ctx.get("queued")),
                      "host": self.name, "status": r.get("status"),
                      "cid": r.get("cid")})
        if not ok:
            A.error(log, "dashboard message (%s send failed)" % self.name,
                    {"sid": ctx.get("sid"), "win": win})
        return r

    def compact(self, fe, win, ctx):
        """Compact the codex conversation — paste `/compact` (codex's own
        summarise command; fires Pre/PostCompact). An atomic bracketed paste +
        Enter through the frontend (fe.paste_text), the mode-proof channel the
        quick commands use; no clipboard-image guard (codex's TUI does not
        auto-attach a clipboard image on paste — `paste_grabs_clipboard_image`
        stays False, unlike Claude Code). Writes the canonical `web-command` row
        (host/status/cid alongside cmd); no confirm menu (that is a Claude
        prompt-cache prompt). Result {status, cid, ok}."""
        r = self._paste(fe, win, "/compact", ctx, "compact")
        self._cmd_row(ctx, r, win, "compact", "")
        return r

    def rename(self, sid, name, ctx):
        """Rename a LIVE codex session — paste codex's own `/rename <name>` (the
        title lands in ~/.codex/state_<N>.sqlite threads.title; the PARKED path is
        title.set_session_title, wired in P3). fe/win ride in `ctx` because the
        gesture signature is sid-keyed (a parked session has no window). Result
        {status, cid, ok}; REJECTED with no window (the caller then takes the
        parked path).

        AND it retitles the kitty tab (`Frontend.set_tab_title`) — the one
        sanctioned caller of that capability. The "no set_tab_title" rule was
        argued for CLAUDE CODE and it INVERTS here: Claude Code re-emits the
        session name as an OSC title at every turn boundary, so retitling would
        make the tab a SECOND writer free to disagree with the session's own.
        codex's TUI emits NO title at all — there is no first writer, so our
        gesture is the only one there is, and without this the tab keeps
        whatever name it had while every other surface shows the new one (the
        reported bug). Nothing can later disagree with it, because nothing else
        ever writes it. Best-effort and AFTER the paste: the paste is the
        rename, this only makes it visible, so a terminal that refuses the
        retitle must not turn a successful rename into a failure."""
        fe, win = ctx.get("fe"), ctx.get("win")
        if not (fe and win):
            return self._rejected()
        r = self._paste(fe, win, "/rename " + name, ctx, "rename", sid=sid)
        log, sdb = ctx.get("log") or "", ctx.get("sdb") or ""
        titled = self._retitle(fe, win, name, ctx) if r.get("ok") else None
        A.state_file(log, sdb, ctx.get("action") or "web-rename",
                     {"win": win, "chars": len(name), "ok": bool(r.get("ok")),
                      "tab": ctx.get("tab") or "", "channel": "tui",
                      "queued": bool(ctx.get("queueing")), "host": self.name,
                      "status": r.get("status"), "cid": r.get("cid"),
                      "tab_title": titled})
        if not r.get("ok"):
            A.error(log, "dashboard rename (%s send failed)" % self.name,
                    {"sid": sid, "win": win})
        return r

    def _retitle(self, fe, win, name, ctx):
        """Set the kitty tab's title to `name` — see rename(). True/False, and
        audited on failure; never raises into the gesture."""
        try:
            ok = bool(fe.set_tab_title(win, name))
        except Exception:
            ok = False
        if not ok:
            A.error(ctx.get("log") or "", "codex rename (tab retitle failed)",
                    {"sid": ctx.get("sid"), "win": str(win)})
        return ok

    def ask(self, fe, win, answers, ctx):
        """Answer codex's OPEN request_user_input dialog (model-nondeterministic)
        by driving its ON-SCREEN dialog — the codex twin of Claude's askdialog,
        keyed on codex's OWN geometry (`Question N/M`, numbered options with a `›`
        cursor, the `enter to submit answer` footer; Claude's askdialog.region()
        returns "" on a codex screen). DOWN walks the cursor onto the chosen
        option, ENTER submits each question in order; a FREE-TEXT answer walks to
        codex's own appended `None of the above` row and types the text as its
        note, which is the ONLY way codex takes an answer that is not one of the
        offered options. `answers` aligns with ctx['questions']
        ([{selected:[label], other:text}]).

        `ctx['chat']` is the card's "chat about this" — DECLINED rather than
        answered (the caller already checked the word against ask_declines()):
        `dialog.decline` submits with every question codex will let it leave
        unanswered, carrying ctx['message'] as the one forced row's note.

        Best-effort: a step that never verifies degrades to INDETERMINATE (audited,
        dialog LEFT OPEN for a retry — never Escape-closed, since codex's Esc
        aborts the turn). Result {status, cid, ok, step?, detail?}."""
        from plugins.codex import dialog
        log, sdb = ctx.get("log") or "", ctx.get("sdb") or ""
        chat = bool(ctx.get("chat"))
        message = (ctx.get("message") or "").strip()
        r = self._ack()
        r["ok"] = True
        try:
            if chat:
                dialog.decline(fe, win, ctx.get("questions") or [], message)
                # the words went in as the decline row's NOTE, so they are
                # already in the tool result — the caller must not ALSO paste
                # them as a follow-up into the turn that just resumed.
                r["message_sent"] = bool(message)
            else:
                dialog.drive(fe, win, ctx.get("questions") or [], answers or [])
        except dialog.CodexAskError as e:
            A.error(log, "codex answer (%s)" % e.step,
                    {"sid": ctx.get("sid"), "win": str(win), "chat": chat,
                     "detail": str(e)})
            r = {"status": INDETERMINATE, "cid": r["cid"], "ok": False,
                 "step": e.step, "detail": str(e)}
        A.state_file(log, sdb, ctx.get("action") or "web-answer",
                     {"win": win, "ok": bool(r.get("ok")),
                      "chat": chat, "host": self.name,
                      "tool_use_id": ctx.get("tool_use_id") or "",
                      "status": r.get("status"), "cid": r.get("cid"),
                      "step": r.get("step")})
        return r

    def plan(self, fe, win, decision, ctx):
        """Decide codex's OPEN plan-mode DECISION picker by driving the on-screen
        picker (plugins/codex/plandialog.py) — codex has NO plan-approval tool,
        the picker is pure TUI (`Implement this plan?` + numbered rows). `decision`
        is one of:
          · {"dismiss": True}         — 'No, stay in Plan mode' (keep planning)
          · {"digit": D, "label": L}  — an APPROVE row ('Yes, implement this
                                        plan' / 'Yes, clear context and
                                        implement'), label-verified on screen.
        Overriding this flips codex's `plan` cap True so the dashboard un-greys
        the plan card's decision buttons. Best-effort like `ask`: an unverified
        step degrades to INDETERMINATE (audited, picker LEFT as-is — never Escape-
        closed, since codex's Esc steps BACK a level). Result {status, cid, ok,
        step?, detail?}."""
        from plugins.codex import plandialog as PD
        log, sdb = ctx.get("log") or "", ctx.get("sdb") or ""
        r = self._ack()
        r["ok"] = True
        decision = decision or {}
        kind = "dismiss" if decision.get("dismiss") else "decide"
        r["kind"] = kind
        try:
            if decision.get("dismiss"):
                PD.dismiss(fe, win)
            else:
                PD.decide(fe, win, decision.get("digit"),
                          decision.get("label") or "")
        except PD.CodexPlanError as e:
            A.error(log, "codex plan (%s)" % e.step,
                    {"sid": ctx.get("sid"), "win": str(win), "detail": str(e)})
            r = {"status": INDETERMINATE, "cid": r["cid"], "ok": False,
                 "kind": kind, "step": e.step, "detail": str(e)}
        A.state_file(log, sdb, ctx.get("action") or "web-plan",
                     {"win": win, "ok": bool(r.get("ok")), "kind": kind,
                      "host": self.name, "status": r.get("status"),
                      "label": decision.get("label") or "",
                      "plan_id": ctx.get("tool_use_id") or ""})
        return r

    def plan_options(self, fe, win, ctx):
        """codex's plan-decision options come from the PENDING read model, not
        the screen: the picker is pure TUI with STATIC rows (unlike Claude's
        dialog, whose labels vary with the session's permission mode), so the
        rollout tail already knows them and no key or capture is needed. Rides
        the `plan` cap."""
        r = self._ack()
        r["ok"] = True
        r["options"] = list(ctx.get("options") or [])
        return r

    def model(self, fe, win, arg, ctx):
        """Switch the codex model via its INTERACTIVE /model picker (codex has no
        `/model <arg>` — plugins/codex/modeldialog.py). `arg` is a codex model id
        (modeldialog.MODEL_CHOICES). PRESERVES the current reasoning level: the
        picker's step 3 would otherwise default to the NEW model's level (so a
        low→switch→medium surprise, since the dashboard's ✦/✧ are independent
        axes), so the gesture reads the current effort from the rollout and
        re-selects it. Falls back to the picker default when the effort can't be
        read. Overriding this flips the `model` cap True. Result {status, cid, ok,
        step?}."""
        from plugins.codex import read as RD
        path = RD._rollout_for(ctx.get("sid") or "", "")
        eff = RD.codex_effort(path) if path else ""
        return self._drive_model(fe, win, ctx, "model", model=arg, effort=eff)

    def effort(self, fe, win, arg, ctx):
        """Switch the reasoning level via the /model picker, KEEPING the current
        model (the picker's `(current)` row) — codex has no live `/effort`. `arg`
        is a token in modeldialog.EFFORT_CHOICES. Overriding this flips the
        `effort` cap True. Result {status, cid, ok, step?}."""
        return self._drive_model(fe, win, ctx, "effort", effort=arg)

    def _drive_model(self, fe, win, ctx, verb, model="", effort=""):
        """Shared body of model/effort: drive the /model picker (which sets both
        axes at once), audited on failure, and write the canonical `web-command`
        row. INDETERMINATE with the picker LEFT as-is on any unverified step
        (codex's Esc steps BACK, so never blind-Esc). A picker that can't be
        driven mid-turn is unlikely (codex refuses `/model` while a turn runs),
        so this is never `queued` — a failure is the caller's 502."""
        from plugins.codex import modeldialog as MD
        r = self._ack()
        r["ok"] = True
        try:
            MD.set_model_effort(fe, win, model=model, effort=effort)
        except MD.CodexModelError as e:
            A.error(ctx.get("log") or "", "codex %s (%s)" % (verb, e.step),
                    {"sid": ctx.get("sid"), "win": str(win), "detail": str(e)})
            r = {"status": INDETERMINATE, "cid": r["cid"], "ok": False,
                 "step": e.step, "detail": str(e)}
        self._cmd_row(ctx, r, win, verb, model or effort)
        return r

    def _cmd_row(self, ctx, r, win, cmd, arg):
        """The `web-command` state_files row every quick command writes — the
        same vocabulary the Claude host's own commands use, plus host/status/cid
        (and `step` when a picker step is what failed). Failures also A.error."""
        log, sdb = ctx.get("log") or "", ctx.get("sdb") or ""
        ok = bool(r.get("ok"))
        row = {"win": win, "cmd": cmd, "arg": arg or "", "ok": ok,
               "tab": ctx.get("tab") or "", "host": self.name,
               "status": r.get("status"), "cid": r.get("cid")}
        if cmd != "compact":
            row["step"] = r.get("step") or ""
        A.state_file(log, sdb, ctx.get("action") or "web-command", row)
        if not ok:
            phrase = ("dashboard command (%s compact send failed)" % self.name
                      if cmd == "compact" else
                      "dashboard command (%s %s: %s)"
                      % (self.name, cmd, r.get("step") or "failed"))
            A.error(log, phrase, {"sid": ctx.get("sid"), "win": win,
                                  "detail": r.get("detail") or ""})

    def _paste(self, fe, win, text, ctx, verb, sid=""):
        """Shared body of compact/rename: an atomic bracketed paste (+ Enter) of a
        slash command into the codex window, audited on failure. ACK/ok on
        success, REJECTED/ok=False on a paste the terminal refused."""
        ok = bool(fe.paste_text(win, text))
        r = self._ack() if ok else self._rejected()
        r["ok"] = ok
        if not ok:
            A.error(ctx.get("log") or "", "codex %s (send failed)" % verb,
                    {"sid": sid or ctx.get("sid"), "win": str(win)})
        return r

    # --- per-host VOCABULARY --------------------------------------------------

    def plan_decisions(self):
        """codex's picker accepts a numbered row or Escape — and NOTHING else.
        There is no free-text 'tell me what to change' row (that is Claude's
        ExitPlanMode dialog), so `feedback` is refused with a 409 naming this
        vocabulary rather than silently dropped: the text the user typed has
        nowhere to go, and pretending otherwise loses it."""
        return ("decide", "dismiss")

    def ask_declines(self):
        """codex's request_user_input dialog has no decline ROW (its Esc ABORTS
        the turn, the opposite of declining) — but it does have a submit that
        leaves questions UNANSWERED (`Submit with unanswered questions?` →
        `Proceed`), which is what "chat about this" actually wants: the tool
        returns, the turn resumes, the composer is yours. `plugins/codex/
        dialog.decline` drives that, so the word IS in this host's vocabulary
        and the card's button no longer 409s. The one thing codex still forces
        is a single answer (the submitting key takes the cursor), delivered on
        its own least-committal row — see decline()'s docstring."""
        return ("chat",)

    # mention / clear_input / turn_live / rewind_modes: likewise inert. codex has
    # no `@path` mention grammar (the caller delivers bare paths), no verified
    # line-kill repertoire for its composer (see title_key's neighbour note
    # below), no rewind, and its turn liveness has a BETTER source than a screen
    # delta — the rollout's own records, which the interrupt already reads
    # (turn_aborted). Wiring turn_live to a rollout probe is future work; None
    # means "trust the tab", which is what codex did before.

    def title_key(self, tpath):
        """The durable rename-override key for a codex ROLLOUT — its filename
        stem (`rollout-<ts>-<uuid>`). Same shape as Claude's transcript stem and
        a different namespace by construction (the uuid), so the two hosts'
        overrides cannot collide in the one prefs map."""
        import os
        base = os.path.basename(tpath or "")
        return base[:-len(".jsonl")] if base.endswith(".jsonl") else ""

    def title_sig(self, tpath):
        """The stat of codex's own state INDEX — the freshness stamp the read
        model folds into its title memo.

        codex is exactly the host the base's "" default is wrong for: the name
        lives in `threads.title`, not in the rollout, so a rename leaves the
        transcript byte-identical and a (path, size) memo serves the old title
        forever — which is what the list page did (measured: threads.title
        said `test`, plugins.session_title agreed, the page did not). See
        title.state_sig for why one stat covers every thread."""
        from plugins.codex import title
        return title.state_sig()

    def lifecycle_end(self, sid, log, reason):
        """Nothing to do: a web-closed codex tab kills the codex process, and
        this plugin's own watcher notices its host pid is gone and runs
        core.hostpane.host_end (pane close + state-DB park + tab clear) on its
        own — the same teardown a terminal-side exit takes. Overridden only to
        record that the question was asked and answered NO, since codex is
        precisely the host with no SessionEnd hook to point at."""
        return None

    # --- launch / lifecycle plumbing (NOT capability-gated) -------------------

    def model_choices(self):
        """codex's ✦ menu models (modeldialog.MODEL_CHOICES) — the label IS the
        picker row AND the gesture arg."""
        from plugins.codex import modeldialog as MD
        return list(MD.MODEL_CHOICES)

    def effort_choices(self):
        """codex's ✧ menu reasoning-effort tokens (modeldialog.EFFORT_CHOICES;
        `xhigh` → the picker's 'Extra high', etc.)."""
        from plugins.codex import modeldialog as MD
        return list(MD.EFFORT_CHOICES)

    def model_default(self):
        """The new-session form's first-ever codex model — the menu's own head
        (modeldialog.MODEL_CHOICES[0]), which is what the client's
        TOOL_MODEL_DEF spelled separately. An EXPLICIT value, never the empty
        "codex default" pseudo-option: you always know what you launched."""
        from plugins.codex import modeldialog as MD
        return MD.MODEL_CHOICES[0]

    def effort_default(self):
        """The new-session form's first-ever codex effort. `low` deliberately —
        codex's own default, and a level its cheapest models actually run at
        (Claude Code's high would be a foreign default applied to another tool,
        which is exactly what the shared client table did before the split)."""
        return "low"

    # model_match: NOT overridden — codex's menu rows ARE full model ids
    # (`gpt-5.6-terra`), so the base "exact" is right. A family compare would
    # mark `gpt-5.4` as the current row for a session running `gpt-5.4-codex`, a
    # model its menu does not offer at all.

    # command_floor / rewind_mode_label: likewise inert. codex has no measured
    # quick-command refusal floor (its /compact takes any conversation) and no
    # rewind at all, so 0 and "" are the honest answers rather than Claude's.

    # model_short: NOT overridden — codex's ids (`gpt-5.4-codex`) ARE their own
    # display spelling, and the base identity is exactly right. It matters that
    # this is now a DECISION rather than an accident: the read model used to run
    # every host's ids through Claude's `short_model` grammar, which splits on
    # `-` and keeps ≤2-digit version parts, so a codex agent card showed a
    # truncated id. model_default_effort is likewise inert on purpose — codex's
    # effort is a per-turn ROLLOUT fact (turn_context, surfaced on ctx["effort"]),
    # not a property of the model, so a default table would be a second, wronger
    # answer than the one the rollout already gives.

    def launch_words(self, opts):
        """The `codex` "$@" tail for a web new-session launch (verified against
        codex-cli 0.144.1). Fresh: `codex -C <cwd> -m <model>
        -c model_reasoning_effort=<eff> "<prompt>"`; resume: the same with the
        `resume <sid>` subcommand+id FIRST (both positionals — SESSION_ID before
        PROMPT), so the prompt trails after the flags and auto-submits. codex has
        NO `--effort` flag (effort is a `-c` config override) and NO `--continue`
        (resuming the most-recent row IS continue). `opts` = {resume, cwd, model,
        effort, prompt}; each fragment is emitted only when set. The command word
        is `codex` (launch_cmd — the base default over `name`)."""
        opts = opts or {}
        resume = opts.get("resume") or ""
        cwd = opts.get("cwd") or ""
        model = opts.get("model") or ""
        effort = opts.get("effort") or ""
        prompt = opts.get("prompt") or ""
        return ((["resume", resume] if resume else [])
                + (["-C", cwd] if cwd else [])
                + (["-m", model] if model else [])
                + (["-c", "model_reasoning_effort=" + effort] if effort else [])
                + ([prompt] if prompt.strip() else []))


def _rec(rollout, ln):
    """rollout.parse_line over one raw bytes line, guarded — the one decoder the
    interrupt verify shares (a torn/undecodable line is simply not a record)."""
    try:
        return rollout.parse_line(ln.decode("utf-8", "replace"))
    except Exception:
        return None


_HOST = None


def get():
    """The process singleton CodexHost (the `host` provider's return)."""
    global _HOST
    if _HOST is None:
        _HOST = CodexHost()
    return _HOST
