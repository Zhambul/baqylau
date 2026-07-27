# dashboard/http/post/state.py — the control-plane POSTs that write STATE and
# type nothing: the composer draft/queue kv, the new-session form's prefs and
# per-directory drafts, the notify mute + global toggle, the mirror view mode,
# hidden directories, and the Web Push subscriptions. Some land in the session's
# own kv, the rest in the durable global prefs store (dashboard/prefs.py).
import time

from core import state as ST
from core.noaudit import load_audit
from dashboard import (prefs)
from dashboard.config import (EFFORTS,
                              MODEL_OK)
from dashboard.read.lists import (dir_live_sessions)
from dashboard.read.session import (session_tasks, tasks_done)
from dashboard.notify.broker import BROKER

A = load_audit()


class _StateMixin:
    """Pure state writes — no terminal is touched by anything here."""

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
        # so its seq survives to reject a later straggler; composer_draft reads
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
        if isinstance(model, str) and MODEL_OK.match(model):
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

    def post_tasks_hide(self, sid):
        """Dismiss (or restore) the session's pinned tasks card — the card's ✕
        (docs/dashboard.md *Web tasks*). Body: `hidden` (bool). PURELY VISUAL,
        like post_view_mode and unlike every other ✕ on the page: no task is
        completed or deleted, and Claude Code's own task records are not written
        at all (the `tasks` kv is a snapshot of them, and the next task-touching
        hook re-stashes it). Writes only the durable global prefs store
        (dashboard/prefs.py), so the dismissal follows you across devices and
        works live AND parked.

        A list with any UNFINISHED task can't be dismissed — a 409, the
        authoritative guard behind the disabled ✕ (tasks_done; the client also
        disables the button, but a stale page could still POST). What gets stored
        is that finished list's ID SET, so a later task — or a re-opened one —
        brings the card back by itself (there is no un-hide button; `hidden:
        false` exists for the page to undo its own optimistic paint after a
        failed write). Behind _post_guard like every control-plane POST; audited
        as a `web-taskshide` state_files row (global — empty log/path like
        notify-mute)."""
        body = self._post_guard()
        if body is None:
            return
        hidden = body.get("hidden")
        if not isinstance(hidden, bool):
            return self._reject_input("web-taskshide", "bad hidden",
                                      "hidden must be a boolean",
                                      {"hidden": hidden}, sid=sid)
        tasks = session_tasks(sid) if hidden else None
        # Not an input error — the body is well-formed — so a distinct `why`,
        # but the same audited-reject shape (no errors row / errwatch chip).
        if hidden and not tasks_done(tasks):
            return self._reject_input(
                "web-taskshide", "tasks unfinished",
                "every task must be completed before the card can be hidden",
                {"sid": sid, "tasks": len(tasks or [])}, code=409, sid=sid)
        ids = [str(t.get("id")) for t in tasks] if hidden else None
        prefs.set_tasks_hidden(sid, ids)
        A.state_file("", "", "web-taskshide",
                     {"sid": sid, "hidden": hidden, "ids": ids or []})
        return self._json({"ok": True, "hidden": hidden})

    def post_view_mode(self, sid):
        """Set the session mirror's VIEW MODE — the filter bar's
        verbose/default/focus control (docs/dashboard.md *View modes*). Body:
        `mode` (one of prefs.VIEW_MODES). Writes the durable global prefs store
        (dashboard/prefs.py), NOT any session/terminal state and emphatically NOT
        Claude Code's own `viewMode` setting: this changes only what the BROWSER
        paints, so it works live AND parked and the TUI is untouched. Behind
        _post_guard like every control-plane POST; audited as a `web-viewmode`
        state_files row (global — empty log/path like notify-mute). Returns the
        stored mode."""
        body = self._post_guard()
        if body is None:
            return
        mode = body.get("mode")
        if mode not in prefs.VIEW_MODES:
            return self._reject_input("web-viewmode", "bad mode",
                                      "mode must be one of %s"
                                      % ", ".join(prefs.VIEW_MODES),
                                      {"mode": mode}, sid=sid)
        prefs.set_view_mode(sid, mode)
        A.state_file("", "", "web-viewmode", {"sid": sid, "mode": mode})
        return self._json({"ok": True, "mode": mode})

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
        BROKER.push("notify-config", {"enabled": on})
        return self._json({"ok": True, "enabled": on})

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
        # degrades to send-all for it — see mru_push_targets).
        dev = body.get("device")
        dev = dev if isinstance(dev, str) and dev else None
        label = body.get("label")
        label = label[:60] if isinstance(label, str) and label else None
        prefs.add_push_subscription(sub, device=dev, label=label)
        A.state_file("", "", "web-push", {"action": "subscribe", "endpoint": ep[:80],
                                          "device": dev, "label": label})
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
