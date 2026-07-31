# dashboard/http/post/dialogs.py — answering the host's own MODAL dialogs from
# the web: the question dialog (the ask card) and the plan dialog (the plan
# card). Both match a pending stash before a single key is pressed, then hand
# the decision to the session's OWNING HOST, whose gesture drives the real
# on-screen dialog (plugins/claude_code/askdialog.py + plandialog.py for Claude
# Code; plugins/codex/dialog.py + plandialog.py for codex) and writes the
# `web-answer`/`web-plan` row. The DECLINE vocabulary is the host's too: an
# unknown word is a 409 naming what that host accepts, never a flag dropped on
# the floor.
from core import state as ST
from core.noaudit import load_audit
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
        card is refused before any key is pressed); either `chat: true` (a
        DECLINE — Claude Code's own "Chat about this" row, which declines +
        invites discussion, the page then focusing its composer; codex spells
        the same word as a submit that leaves the questions UNANSWERED, having
        no decline row at all; 409 for a host whose `ask_declines()` has no such
        word, since a decline that cannot be delivered must not be silently
        answered instead) or `answers` — a list
        aligned with the stash's questions: {"selected": [labels…], "other":
        "text"} per question (multiSelect may combine both; single-select uses
        one or the other).

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
        host = self._gesture_host(sid)
        # the DECLINE vocabulary is the host's own (Claude Code: "chat"). A word
        # this host has no row for is refused, naming what it does accept — the
        # codex branch used to drop the flag and ANSWER the question instead.
        if chat and "chat" not in host.ask_declines():
            return self._reject_input(
                "web-answer", "decline unsupported",
                "this session's tool has no decline for a question "
                "(accepts: %s)" % _vocab(host.ask_declines()),
                {"chat": True}, code=409, sid=sid)
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
        # The dialog itself is the host's: each drives its OWN geometry
        # (Claude Code's ☐/☒ chip bar + numbered rows; codex's `Question N/M` +
        # `›` cursor — Claude's region() returns "" on a codex screen), leaves an
        # unverified dialog OPEN rather than Escape-closing it, and writes the
        # `web-answer` row.
        tid = pending.get("tool_use_id") or ""
        msg = body.get("message")
        message = msg.strip() if (chat and isinstance(msg, str)) else ""
        res = host.ask(fe, win, answers or [], {
            "sid": sid, "log": log, "sdb": sdb, "action": "web-answer",
            "verb": "answer", "chat": chat, "questions": questions,
            "message": message, "tool_use_id": tid})
        if self._gesture_declined(res, sid, "web-answer", "ask",
                                  extra={"win": win, "chat": chat}):
            return
        if not res.get("ok"):
            # a kv the driver PROVED stale self-heals (the dialog resolved in
            # the terminal); a host whose pending is derived read-side from its
            # own transcript has no kv to heal and says so with no `step` of
            # that kind — heal_stash is a no-op for a step it doesn't know.
            heal_stash(sid, log, sdb, "ask-pending", res.get("step") or "")
            return self._json({"error": res.get("detail") or "answer failed",
                               "step": res.get("step") or "drive"}, 409)
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
        #
        # …UNLESS the host already put those words INSIDE the dialog it just
        # declined (`message_sent` — codex types them as its decline row's note,
        # so they ride the tool RESULT). A second delivery there would paste the
        # same sentence into the turn that decline just resumed.
        resp = {"ok": True, "chat": chat}
        if message and res.get("message_sent"):
            resp["message_sent"] = True
        elif message:
            out = host.deliver(fe, win, msg, {
                "sid": sid, "log": log, "sdb": sdb, "via": "ask-chat"})
            resp["message_sent"] = bool(out.get("ok"))
        return self._json(resp)

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
        # WHERE the options come from is the host's call: Claude Code reads them
        # off the live screen (its labels vary with the session's permission
        # mode), codex hands back the static rows its pending already carries.
        res = self._gesture_host(sid).plan_options(fe, win, {
            "sid": sid, "log": log, "sdb": sdb,
            "options": pending.get("options") or []})
        if self._gesture_declined(res, sid, "web-plan", "plan"):
            return
        if not res.get("ok"):
            heal_stash(sid, log, sdb, "plan-pending", res.get("step") or "")
            return self._json({"error": res.get("detail") or "no options",
                               "step": res.get("step") or ""}, 409)
        return self._json({"ok": True, "options": res.get("options") or []})

    def post_plan_decision(self, sid):
        """Decide the OPEN plan dialog from the web (docs/dashboard.md, *Web
        plan mode*): drives the TUI's own dialog via dashboard/plandialog.

        Body (one of the owning host's `plan_decisions()`, after `tool_use_id`
        matching the `plan-pending` stash): `digit` + `label` — press that
        decision row, verified against the live screen (label drift = 409,
        nothing pressed); `feedback` — the free-text "tell it what to change"
        row (Claude Code only; a host without one 409s naming its vocabulary
        rather than swallowing the text); `dismiss: true` — Escape, the TUI's
        own reject-and-keep-planning.

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
        host = self._gesture_host(sid)
        vocab = host.plan_decisions()
        tid = pending.get("tool_use_id") or pending.get("plan_id") or ""
        # the body's SHAPE names the decision; the host's vocabulary says
        # whether it has such a row. An unknown shape is the old 400 ("no
        # action"); a KNOWN shape this host doesn't offer is a 409 naming the
        # ones it does — codex's picker has no free-text row, and swallowing the
        # feedback would lose what the user typed.
        if body.get("dismiss"):
            kind, decision = "dismiss", {"dismiss": True}
        elif isinstance(body.get("feedback"), str) \
                and body["feedback"].strip():
            kind, decision = "feedback", {"feedback": body["feedback"]}
        elif body.get("digit") and isinstance(body.get("label"), str):
            kind = "decide"
            decision = {"digit": str(body["digit"]), "label": body["label"]}
        else:
            return self._reject_input(
                "web-plan", "no action", "need " + _vocab_help(vocab),
                {"keys": sorted(body)}, log=log, path=sdb)
        if kind not in vocab:
            return self._reject_input(
                "web-plan", "%s unsupported" % kind,
                "this session's tool has no %s for a plan (needs %s)"
                % (kind, _vocab_help(vocab)),
                {"kind": kind}, code=409, log=log, path=sdb)
        res = host.plan(fe, win, decision, {
            "sid": sid, "log": log, "sdb": sdb, "action": "web-plan",
            "verb": "plan", "tool_use_id": tid})
        if self._gesture_declined(res, sid, "web-plan", "plan",
                                  extra={"win": win, "kind": kind}):
            return
        if not res.get("ok"):
            heal_stash(sid, log, sdb, "plan-pending", res.get("step") or "")
            return self._json({"error": res.get("detail")
                               or "plan decision failed",
                               "step": res.get("step") or ""}, 409)
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


_DECISION_WORDS = {"decide": "digit+label", "feedback": "feedback",
                   "dismiss": "dismiss"}


def _vocab(words):
    """A host's vocabulary as prose for an error message — "none" for a host
    that has no word at all, which is the whole point of naming it."""
    return ", ".join(words) if words else "none"


def _vocab_help(words):
    """The "need …" half of a plan-decision 400/409, built from the host's OWN
    `plan_decisions()` in its declared order — so the message names what THIS
    tool accepts instead of a fixed list that is right for one of them."""
    parts = [_DECISION_WORDS.get(w, w) for w in words]
    if not parts:
        return "a decision this tool supports"
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return "%s or %s" % (parts[0], parts[1])
    return "%s, or %s" % (", ".join(parts[:-1]), parts[-1])
