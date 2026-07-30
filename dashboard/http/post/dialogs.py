# dashboard/http/post/dialogs.py — answering the TUI's own MODAL dialogs from
# the web: AskUserQuestion (the ask card) and ExitPlanMode (the plan card). Both
# match a pending kv stash before a single key is pressed, then drive the real
# on-screen dialog through dashboard/askdialog.py / plandialog.py.
from functools import partial

from core import state as ST
from core.noaudit import load_audit
from dashboard import (askdialog, plandialog)
from core.screendrive import clip_screen
from dashboard.control import launch
from dashboard.read.mirror import (drop_stash, heal_stash)
from dashboard.read.session import (ask_pending, plan_pending)

A = load_audit()


class _DialogMixin:
    """The ask + plan card endpoints, and their shared stash guards."""

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
        pending = ask_pending(sid)
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
        is a `web-answer` state_files row, failures also an A.error. The card
        itself clears via the SSE `ask` event when the stash drops: for an
        `answers` submission that is the PostToolUse, the true end-to-end
        signal; a `chat` DECLINE fires no hook, so this endpoint drops it (see
        the drop_stash call below)."""
        body = self._post_guard()
        if body is None:
            return
        # refuse when the owning host can't answer a dialog (no-op for
        # claude_code — its `ask` cap is True; byte-identical)
        if self._caps_guard(sid, "ask", "web-answer"):
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
        fe = launch.frontend()
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
        # NON-claude host (codex) answers its OWN request_user_input dialog through
        # its HostControl.ask gesture (codex's dialog geometry differs — Claude's
        # askdialog.region() returns "" on it). None for a claude_code / unprovable
        # session, so the byte-identical inline askdialog path below runs unchanged.
        host = self._gesture_host(sid)
        if host is not None:
            return self._host_answer(host, sid, questions, answers or [], chat,
                                     log, sdb, fe, win, pending)
        try:
            askdialog.drive(fe, win, questions, answers or [], chat=chat)
        except askdialog.AskError as e:
            ctx = {"sid": sid, "win": win, "chat": chat, "detail": str(e)}
            if e.screen is not None:      # the pixels the failing step saw
                ctx["screen"] = clip_screen(e.screen)
            A.error(log, "dashboard answer (%s)" % e.step, ctx)
            A.state_file(log, sdb, "web-answer",
                         {"win": win, "ok": False, "chat": chat,
                          "step": e.step,
                          "tool_use_id": pending.get("tool_use_id") or ""})
            heal_stash(sid, log, sdb, "ask-pending", e.step)
            return self._json({"error": str(e), "step": e.step}, 409)
        A.state_file(log, sdb, "web-answer",
                     {"win": win, "ok": True, "chat": chat,
                      "tool_use_id": pending.get("tool_use_id") or ""})
        # "Chat about this" DECLINES the questions, and a decline fires no hook —
        # the plan card's bug in the other dialog (see post_plan_decision), and
        # sharper here, because this path's whole purpose is to hand you the
        # composer ("questions dismissed — type your message below") while the
        # lingering stash made the modal gate refuse that very message. `drive`
        # waited for the dialog AND the review screen to be gone before
        # returning, so the stash is provably stale. The draft dies with its
        # question, exactly as ask_fmt couples them.
        #
        # ANSWERS are left alone: their PostToolUse owns that clear (and may end
        # on the review screen rather than a closed dialog, so this could not
        # prove staleness there anyway).
        if chat:
            for key in ("ask-pending", "ask-draft"):
                drop_stash(sid, log, sdb, key, "web decline (chat)")
        # a PREVIEW-layout question has no typed-answer row (askdialog
        # _require_type_row), so the card routes a TYPED answer through 'Chat
        # about this' AND carries the typed text here as `message`: once the
        # dialog is dismissed (drive waited for that), deliver it as the
        # follow-up so the user's custom answer reaches the session as a
        # normal message (docs/dashboard.md, *Web ask*). Only with chat.
        msg = body.get("message")
        resp = {"ok": True, "chat": chat}
        if chat and isinstance(msg, str) and msg.strip():
            clip = launch.clear_clipboard_image()      # clipboard-image guard, as post_message
            sent = bool(fe.paste_text(win, msg))
            A.state_file(log, sdb, "web-send",
                         {"win": win, "chars": len(msg), "ok": sent,
                          "via": "ask-chat", "clip": clip})
            if not sent:
                A.error(log, "dashboard answer-chat message (send failed)",
                        {"sid": sid, "win": win})
            resp["message_sent"] = sent
        return self._json(resp)

    def _host_answer(self, host, sid, questions, answers, chat, log, sdb, fe,
                     win, pending):
        """Answer a NON-claude host's dialog (codex request_user_input) through
        its HostControl.ask gesture, which navigates codex's OWN dialog geometry
        (plugins/codex/dialog.py). `chat` (Claude's 'Chat about this' decline) has
        no codex analog, so it is ignored — codex answers by option selection /
        notes. The gesture catches its own driver errors, A.errors an INDETERMINATE
        degrade with the dialog left open, and hands back {status, ok, step?}; this
        writes the canonical `web-answer` row (host/status/cid alongside) and the
        reply. No heal_stash: codex's pending is derived read-side from the rollout,
        not a kv to self-heal (the next payload re-reads the tail)."""
        ctx = {"sid": sid, "log": log, "sdb": sdb, "chat": chat,
               "questions": questions,
               "tool_use_id": pending.get("tool_use_id") or ""}
        res = host.ask(fe, win, answers, ctx)
        ok = bool(res.get("ok"))
        A.state_file(log, sdb, "web-answer",
                     {"win": win, "ok": ok, "chat": chat, "host": host.name,
                      "tool_use_id": pending.get("tool_use_id") or "",
                      "status": res.get("status"), "cid": res.get("cid"),
                      "step": res.get("step")})
        if not ok:
            return self._json({"error": res.get("detail") or "answer failed",
                               "step": res.get("step") or "drive"}, 409)
        return self._json({"ok": True, "chat": chat})

    def _plan_guard(self, sid):
        """The shared head of the two plan endpoints: guard the POST, match
        the stash, resolve the live window. Returns (body, pending, fe, win,
        log, sdb) — or (None, …) after sending the error response."""
        none = (None,) * 6
        body = self._post_guard()
        if body is None:
            return none
        # refuse when the owning host can't decide a plan dialog — covers both
        # plan endpoints through their shared head (no-op for claude_code)
        if self._caps_guard(sid, "plan", "web-plan"):
            return none
        log, sdb = self._audit_target(sid)[1:]
        pending = plan_pending(sid)
        if not pending:
            self._json({"error": "no pending plan"}, 409)
            return none
        # match on whichever id the pending carries — claude_code's ExitPlanMode
        # `tool_use_id`, or codex's `plan_id` (its plan has no tool_use_id) — so
        # the staleness guard works for both hosts.
        pend_id = pending.get("tool_use_id") or pending.get("plan_id") or ""
        body_id = body.get("tool_use_id") or body.get("plan_id") or ""
        if body_id != pend_id:
            self._json({"error": "plan expired — a newer plan replaced it "
                        "(refresh)"}, 409)
            return none
        fe = launch.frontend()
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
        # a NON-claude host (codex) carries its decision options in the pending
        # read model (they're static — the picker is pure TUI, not a permission-
        # varying dialog), so no screen read is needed. None for claude_code, so
        # the byte-identical plandialog.options path below runs unchanged.
        if self._gesture_host(sid) is not None:
            return self._json({"ok": True, "options": pending.get("options") or []})
        try:
            opts = plandialog.options(fe, win)
        except plandialog.PlanError as e:
            heal_stash(sid, log, sdb, "plan-pending", e.step)
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
        `web-plan` state_files row, failures also an A.error. The card clears
        via the SSE `plan` event when the stash drops: an approval's own
        PostToolUse, or — for the two DECLINE kinds, which fire no hook — this
        endpoint itself (see the drop_stash call below; waiting for "the turn
        boundary after a reject" is what blocked the composer for 9 minutes)."""
        body, pending, fe, win, log, sdb = self._plan_guard(sid)
        if body is None:
            return
        # a NON-claude host (codex) decides its OWN plan picker through its
        # HostControl.plan gesture (codex's picker geometry differs — Claude's
        # plandialog keys on ExitPlanMode's dialog). None for claude_code / an
        # unprovable path, so the byte-identical inline path below runs unchanged.
        host = self._gesture_host(sid)
        if host is not None:
            return self._host_plan(host, sid, body, pending, log, sdb, fe, win)
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
            heal_stash(sid, log, sdb, "plan-pending", e.step)
            return self._json({"error": str(e), "step": e.step}, 409)
        A.state_file(log, sdb, "web-plan",
                     {"win": win, "ok": True, "kind": kind,
                      "label": body.get("label") or "", "tool_use_id": tid})
        # A DECLINE has no closing hook and sends no message, so NOTHING else
        # drops the stash until the next turn boundary — which after a decline is
        # however long the model's continuation runs, or NEVER if you interrupt
        # it. Two things then break at once: the card sits stale, and the
        # composer's modal gate 409s every send ("I couldn't send you a message
        # before or after rejection" — session e683c445, 2026-07-30: a dismiss at
        # 10:36:21, two sends 30s later both `blocked: modal`, the 104-char
        # message lost, and it only came back 9 minutes on when an unrelated turn
        # boundary finally cleared it). So drop it HERE — the driver verified the
        # dialog is GONE before returning (plandialog's `submit` step), which is
        # exactly what the stash claims otherwise.
        #
        # Declines ONLY: an approval's own PostToolUse is the single owner of
        # that clear and it fires reliably, so this fills the gap where there is
        # no owner rather than taking the clear away from one that works.
        if kind in ("feedback", "dismiss"):
            drop_stash(sid, log, sdb, "plan-pending", "web decline (%s)" % kind)
        return self._json({"ok": True, "kind": kind})

    def _host_plan(self, host, sid, body, pending, log, sdb, fe, win):
        """Decide a NON-claude host's plan picker (codex) through HostControl.plan.
        Body shapes: {dismiss:true} → keep planning; {digit, label} → approve
        that decision row. codex's picker has NO free-text 'feedback' row (the
        card hides that box off-Claude), so only those two arrive. The gesture
        catches its own driver errors, returns {status, ok, step?}, and this
        writes the canonical `web-plan` state_files row (host/status/plan_id
        alongside) + the reply. No heal_stash: codex's pending is derived
        read-side from the rollout, not a kv to self-heal (the next payload
        re-reads the tail)."""
        pid = pending.get("plan_id") or pending.get("tool_use_id") or ""
        if body.get("dismiss"):
            kind, decision = "dismiss", {"dismiss": True}
        elif isinstance(body.get("label"), str) and body["label"].strip():
            kind = "decide"
            decision = {"digit": body.get("digit"), "label": body["label"]}
        else:
            return self._reject_input(
                "web-plan", "no action", "need digit+label or dismiss",
                {"keys": sorted(body)}, log=log, path=sdb)
        res = host.plan(fe, win, decision, {"sid": sid, "log": log, "sdb": sdb})
        ok = bool(res.get("ok"))
        A.state_file(log, sdb, "web-plan",
                     {"win": win, "ok": ok, "kind": kind, "host": host.name,
                      "status": res.get("status"),
                      "label": body.get("label") or "", "plan_id": pid})
        if not ok:
            return self._json({"error": res.get("detail")
                               or "plan decision failed",
                               "step": res.get("step") or ""}, 409)
        return self._json({"ok": True, "kind": kind})
