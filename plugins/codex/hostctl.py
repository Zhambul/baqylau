# plugins/codex/hostctl.py — codex's HostControl adapter.
#
# codex as a first-class HOST tool (plugins.host.HostControl) — what
# plugins.host_of / host_for(sid) / host_caps("codex") hand back for a codex
# session, and what the dashboard reads to know which control buttons to offer.
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
# time `-c` only). `send` is a generic paste, not a gesture (post_message is never
# caps-gated), so it needs no override. Launch/resume plumbing (below) was live
# since P6 and is NOT gesture-gated.
#
# The gesture bodies use ONLY the frontend (`fe`) + this plugin's own rollout
# parse — never dashboard code (the layering rule forbids a plugin importing the
# dashboard). That is WHY the codex screen driver lives at plugins/codex/dialog.py
# rather than beside dashboard/askdialog.py: the whole gesture, screen driver
# included, sits behind HostControl so the dashboard only ever calls
# host.<gesture> (docs/codex.md *Codex control gestures*).
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

    # A STANDALONE codex session's own ops ARE its stream: the same tailer runs
    # whether codex is the session or a sidecar inside a Claude one, so the lead's
    # turns are painted as ⇢/✎/⋯/⇠ prose blocks AND re-bubbled by
    # plugins.conversation. The web session view drops the ops half. See
    # HostControl.lead_prose — this is the trait that replaced the read model's
    # `owns_by(transcript) == "codex"` string compare.
    lead_prose = True

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

        `ctx['rollout']` is the session's rollout path (the verify source). Result
        {status, cid, ok, verified, steered, tries}: ACK when turn_aborted was
        observed, INDETERMINATE when the Esc landed but no record appeared (audited
        — the codex-interrupt anomaly signature), REJECTED when nothing could be
        pressed."""
        rp = ctx.get("rollout") or ""
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
            A.error(ctx.get("log") or "", "codex interrupt (no turn_aborted)",
                    {"sid": ctx.get("sid"), "win": str(win), "tries": tries})
        r["status"] = ACK if verified else (INDETERMINATE if ok else REJECTED)
        r["ok"], r["verified"] = ok, verified
        r["steered"], r["tries"] = steered, tries
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

    def compact(self, fe, win, ctx):
        """Compact the codex conversation — paste `/compact` (codex's own
        summarise command; fires Pre/PostCompact). An atomic bracketed paste +
        Enter through the frontend (fe.paste_text), the mode-proof channel the
        quick commands use; no clipboard-image guard (codex's TUI does not
        auto-attach a clipboard image on paste, unlike Claude Code). Result
        {status, cid, ok}."""
        return self._paste(fe, win, "/compact", ctx, "compact")

    def rename(self, sid, name, ctx):
        """Rename a LIVE codex session — paste codex's own `/rename <name>` (the
        title lands in ~/.codex/state_<N>.sqlite threads.title; the PARKED path is
        title.set_session_title, wired in P3). fe/win ride in `ctx` because the
        gesture signature is sid-keyed (a parked session has no window). Result
        {status, cid, ok}; REJECTED with no window (the caller then takes the
        parked path)."""
        fe, win = ctx.get("fe"), ctx.get("win")
        if not (fe and win):
            return self._rejected()
        return self._paste(fe, win, "/rename " + name, ctx, "rename", sid=sid)

    def ask(self, fe, win, answers, ctx):
        """Answer codex's OPEN request_user_input dialog (plan-mode-only,
        model-nondeterministic) by driving its ON-SCREEN dialog — the codex twin
        of Claude's askdialog, keyed on codex's OWN geometry (`Question N/M`,
        numbered options with a `›` cursor, the `enter to submit answer` footer;
        Claude's askdialog.region() returns "" on a codex screen). DOWN walks the
        cursor onto the chosen option, ENTER submits each question in order.
        `answers` aligns with ctx['questions'] ([{selected:[label], other:text}]).

        Best-effort: a step that never verifies degrades to INDETERMINATE (audited,
        dialog LEFT OPEN for a retry — never Escape-closed, since codex's Esc
        aborts the turn). Result {status, cid, ok, step?, detail?}."""
        from plugins.codex import dialog
        r = self._ack()
        r["ok"] = True
        try:
            dialog.drive(fe, win, ctx.get("questions") or [], answers or [])
        except dialog.CodexAskError as e:
            A.error(ctx.get("log") or "", "codex answer (%s)" % e.step,
                    {"sid": ctx.get("sid"), "win": str(win), "detail": str(e)})
            return {"status": INDETERMINATE, "cid": r["cid"], "ok": False,
                    "step": e.step, "detail": str(e)}
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
        r = self._ack()
        r["ok"] = True
        decision = decision or {}
        try:
            if decision.get("dismiss"):
                PD.dismiss(fe, win)
            else:
                PD.decide(fe, win, decision.get("digit"),
                          decision.get("label") or "")
        except PD.CodexPlanError as e:
            A.error(ctx.get("log") or "", "codex plan (%s)" % e.step,
                    {"sid": ctx.get("sid"), "win": str(win), "detail": str(e)})
            return {"status": INDETERMINATE, "cid": r["cid"], "ok": False,
                    "step": e.step, "detail": str(e)}
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
        axes at once), audited on failure. INDETERMINATE with the picker LEFT as-
        is on any unverified step (codex's Esc steps BACK, so never blind-Esc)."""
        from plugins.codex import modeldialog as MD
        r = self._ack()
        r["ok"] = True
        try:
            MD.set_model_effort(fe, win, model=model, effort=effort)
        except MD.CodexModelError as e:
            A.error(ctx.get("log") or "", "codex %s (%s)" % (verb, e.step),
                    {"sid": ctx.get("sid"), "win": str(win), "detail": str(e)})
            return {"status": INDETERMINATE, "cid": r["cid"], "ok": False,
                    "step": e.step, "detail": str(e)}
        return r

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

    # model_short: NOT overridden — codex's ids (`gpt-5.4-codex`) ARE their own
    # display spelling, and the base identity is exactly right. It matters that
    # this is now a DECISION rather than an accident: the read model used to run
    # every host's ids through Claude's `short_model` grammar, which splits on
    # `-` and keeps ≤2-digit version parts, so a codex agent card showed a
    # truncated id. model_default_effort is likewise inert on purpose — codex's
    # effort is a per-turn ROLLOUT fact (turn_context, surfaced on ctx["effort"]),
    # not a property of the model, so a default table would be a second, wronger
    # answer than the one the rollout already gives.

    def resume_words(self, sid):
        """`codex resume <sid>` — codex's own conversation-resume argv (a codex
        session id IS its rollout uuid). The new-session/resume-&-send path
        composes a relaunch from this; [] when there is no sid."""
        return ["resume", sid] if sid else []

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
