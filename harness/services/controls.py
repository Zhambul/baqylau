"""The one control dispatch point: a gesture in, its harness's outcome out."""

from __future__ import annotations

import time

from diagnostics import record
from harness.contract import HarnessReactorContext
from harness.models import (
    ControlContext,
    ControlOutcome,
    ControlRequest,
    ControlResult,
)
from engine.projections import SessionQueries
from engine.store.sessions import SessionStore
from terminal.adapter import TerminalAdapter
from terminal.contract import TerminalPlugin


# Every control gesture's OUTCOME, recorded at the one dispatch point every
# harness and every gesture passes through (`HarnessControlService.execute`).
#
# It exists because a failed gesture used to leave NOTHING in the audit. Measured
# (session 01a0037d, 2026-08-15 11:36): a web model switch failed inside its
# harness's screen driver and the only trace anywhere was the browser's own
# `command.ok` row carrying `status: 202` — and 202 is `indeterminate`, i.e. the
# FAILURE code. The reason string went into the HTTP response body and nowhere
# else, so the driver's own step name — which its error type carries expressly
# "for the audit" — was unrecoverable, and the bug could only be named because
# the stuck dialog happened to still be on screen an hour later.
#
# `status` is the diagnostic column, and `indeterminate` is the interesting
# value: the request was understood and the gesture was attempted, but the
# harness never confirmed it — a screen driver that bailed, a paste the TUI
# refused. `rejected` is a guard declining up front and `acknowledged` is the
# happy path. A raised gesture records `status: "raised"` before re-raising, so
# the row exists even when the HTTP layer turns it into a 500.
# The row carries the SESSION ID in its own column, unlike the browser-event
# rows, whose session lives inside the JSON — those are invisible to the obvious
# `WHERE session_id = ?` triage query, which is how this gesture first read as
# "no audit at all".
def _audit_control(request: ControlRequest, outcome, elapsed: float) -> None:
    try:
        record.state_file(
            str(request.session_id),
            "",
            "control",
            {
                "control": getattr(request, "control_name", ""),
                "request_id": request.request_id,
                "status": outcome.status if outcome is not None else "raised",
                "reason": (outcome.reason if outcome is not None else "") or "",
                "ms": round(elapsed * 1000),
            },
        )
    except Exception:
        # The one sanctioned silent swallow: this IS the recording path, so
        # there is nowhere left to record that it failed, and a locked audit DB
        # must never take down the gesture it is only observing.
        pass


class HarnessControlService(HarnessReactorContext):
    def __init__(
        self,
        sessions: SessionStore,
        terminal: TerminalAdapter,
        plugin: TerminalPlugin,
        queries: SessionQueries,
    ) -> None:
        self.sessions = sessions
        self.terminal = terminal
        self.plugin = plugin
        self.queries = queries

    def execute(self, request: ControlRequest) -> ControlOutcome:
        started = time.monotonic()
        try:
            outcome = self._execute(request)
        except Exception:
            _audit_control(request, None, time.monotonic() - started)
            raise
        _audit_control(request, outcome, time.monotonic() - started)
        return outcome

    def _execute(self, request: ControlRequest) -> ControlOutcome:
        session = self.sessions.find_by_id(request.session_id)
        if session is None:
            return ControlResult(request.request_id, "rejected", "unknown session")
        plugin = session.plugin
        if plugin is None or plugin.controller is None:
            return ControlResult(request.request_id, "rejected", "unsupported control")
        cursor = self.queries.canonical_store.through(request.session_id).latest_cursor or 0
        summary = self.queries.summary(request.session_id, cursor)
        attention_id = getattr(request, "attention_id", None)
        pending_attention = next(
            (
                pending.request
                for pending in self.queries.attention(request.session_id, cursor).pending
                if pending.request.attention_id == attention_id
            ),
            None,
        )
        return plugin.controller.execute(
            request,
            ControlContext(
                session=session,
                terminal=self.plugin,
                terminal_window_id=self.terminal.window_for_session(request.session_id),
                current_model=summary.model if summary is not None else None,
                current_effort=summary.effort if summary is not None else None,
                current_account=summary.account if summary is not None else None,
                pending_attention=pending_attention,
            ),
        )
