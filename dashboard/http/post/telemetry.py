# dashboard/http/post/telemetry.py — the write-only beacons: what the BROWSER
# saw that the server cannot (optimistic-UI lifecycle, an observed gesture
# failure, the per-gesture transport/SSE/JS-error timeline) plus the presence
# heartbeats. Audit-only or in-memory; none of it writes session state or
# surfaces an error to the page (docs/dashboard.md, *Frontend audit*).

from core.noaudit import load_audit
from dashboard.config import (CLIENTLOG_MAX)
from dashboard.http.base import valid_sid
from dashboard.notify.presence import mark_device, mark_viewing

A = load_audit()


class _TelemetryMixin:
    """The client-side beacons and the presence heartbeats."""

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
            esid = esid if isinstance(esid, str) and valid_sid(esid) else ""
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

    def post_viewing(self, sid):
        """Presence heartbeat: the page reports it is looking at session `sid`
        RIGHT NOW (docs/dashboard.md *Telegram alerts*). The client sends it on
        a timer ONLY while the page is visible + focused + showing this session,
        so the mere arrival is the signal — it refreshes the in-memory
        `_VIEWING` deadline (`mark_viewing`, fresh for VIEW_TTL_S) that the
        deferred Telegram alert checks at send time to tell 'watching the
        dashboard' from 'walked away'. Types NOTHING and writes NO session
        state; NOT audited per-beat (ephemeral live-only presence, like the SSE
        connection — the SUPPRESS it drives lands the notify-suppress row).
        Behind _post_guard like every control-plane POST; always 200 (an empty
        `{}` body is fine — the URL's sid IS the payload)."""
        if self._post_guard() is None:
            return
        mark_viewing(sid)
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
            mark_device(dev)
        sid = body.get("sid")
        if isinstance(sid, str) and sid:
            mark_viewing(sid)
        return self._json({"ok": True})
