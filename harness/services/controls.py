"""The one control dispatch point: a gesture in, its harness's outcome out."""

from __future__ import annotations

import time

from diagnostics.recorder import AuditRecorder
from harness.contract import HarnessReactorContext
from harness.models import (
    ControlContext,
    ControlOutcome,
    ControlRequest,
    ControlResult,
    Interrupt,
    InterruptRegistry,
)
from engine.projections import SessionQueries
from repository.contract.sessions import SessionRepository
from repository.contract.usage import AccountUsageRepository
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
def _audit_control(audit, request: ControlRequest, outcome, elapsed: float) -> None:
    try:
        audit.state_file(
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
        sessions: SessionRepository,
        terminal: TerminalAdapter,
        plugin: TerminalPlugin,
        queries: SessionQueries,
        account_usage: AccountUsageRepository,
        audit: AuditRecorder,
        interrupts: InterruptRegistry,
    ) -> None:
        self.sessions = sessions
        self.terminal = terminal
        self.plugin = plugin
        self.queries = queries
        self.account_usage = account_usage
        self.audit = audit
        self.interrupts = interrupts

    def execute(self, request: ControlRequest) -> ControlOutcome:
        started = time.monotonic()
        try:
            outcome = self._execute(request)
        except Exception:
            _audit_control(self.audit, request, None, time.monotonic() - started)
            raise
        _audit_control(self.audit, request, outcome, time.monotonic() - started)
        # An interrupt the harness acknowledged but did not corroborate in its
        # own evidence: nothing else will ever tell the interpreter this turn
        # ended, so mark it for the registry's fallback fact. A harness whose
        # translator will read a native abort record on its own next pass
        # sets `corroborated=True` and is never marked.
        if (
            isinstance(request, Interrupt)
            and outcome.status == "acknowledged"
            and not getattr(outcome, "corroborated", False)
        ):
            self.interrupts.mark(request.session_id)
        return outcome

    def _execute(self, request: ControlRequest) -> ControlOutcome:
        session = self.sessions.find(request.session_id)
        if session is None:
            return ControlResult(request.request_id, "rejected", "unknown session")
        plugin = session.plugin
        if plugin is None or plugin.controller is None:
            return ControlResult(request.request_id, "rejected", "unsupported control")
        cursor = self.queries.canonical_events.latest_cursor() or 0
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
                account_usage=self.account_usage.snapshots(),
            ),
        )
