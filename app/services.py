"""Application controls and native live-state reads routed by persisted ownership."""

from __future__ import annotations

import time
from typing import Protocol

from core import audit

from contracts.harness import (
    ControlContext,
    ControlOutcome,
    ControlRequest,
    ControlResult,
    HarnessCatalogSnapshot,
    LaunchRequest,
    LaunchRejected,
    LaunchResult,
    QueryContext,
    RawEventDelivery,
    TerminalInputState,
    TerminalSessionState,
    UsageRow,
)
from contracts.terminal import SessionTerminal, TabRequest, TerminalControl, TerminalScreen
from domain.ids import SessionId
from runtime.registry import HarnessRegistry
from runtime.projections import SessionQueries
from app.usage import UsageSource


class ApplicationHostControl(Protocol):
    def ensure_running(self) -> None: ...


class HarnessHookService:
    def __init__(
        self,
        registry: HarnessRegistry,
        delivery: RawEventDelivery,
        controls: HarnessControlService,
        host: ApplicationHostControl,
    ) -> None:
        self.registry = registry
        self.delivery = delivery
        self.controls = controls
        self.host = host

    def receive(self, harness: str, payload: bytes) -> bytes:
        plugin = self.registry.plugin(harness)
        if plugin.hook is None:
            raise ValueError(f"harness {harness!r} does not accept hooks")
        intake = plugin.hook.receive(payload)
        self.registry.event_store.register_session(harness, intake.session)
        for raw_event in intake.raw_events:
            if raw_event.harness != harness:
                raise ValueError("hook observation harness does not match its plugin")
            self.delivery.deliver(raw_event)
        for request in intake.controls:
            if request.session_id != intake.session.session_id:
                raise ValueError("hook control does not belong to its session")
            outcome = self.controls.execute(request)
            if outcome.status != "acknowledged":
                reason = outcome.reason or outcome.status
                raise RuntimeError(
                    f"hook control {request.control_name!r} did not complete: {reason}"
                )
        self.host.ensure_running()
        for action in intake.actions:
            action.start()
        return intake.output


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


class HarnessControlService:
    def __init__(
        self,
        registry: HarnessRegistry,
        terminal: TerminalControl,
        queries: SessionQueries,
    ) -> None:
        self.registry = registry
        self.terminal = terminal
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
        plugin = self.registry.plugin_for_session(request.session_id)
        if plugin.controller is None:
            return ControlResult(request.request_id, "rejected", "unsupported control")
        session = self.registry.registered_session(request.session_id).session
        cursor = self.queries.event_store.through(request.session_id).latest_cursor or 0
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
                terminal=self.terminal,
                current_model=summary.model if summary is not None else None,
                current_effort=summary.effort if summary is not None else None,
                current_account=summary.account if summary is not None else None,
                pending_attention=pending_attention,
            ),
        )


class HarnessLauncherService:
    def __init__(self, registry: HarnessRegistry, terminal: SessionTerminal) -> None:
        self.registry = registry
        self.terminal = terminal

    def launch(self, harness: str, request: LaunchRequest) -> LaunchResult:
        plugin = self.registry.plugin(harness)
        if plugin.launcher is None:
            return LaunchResult("rejected", reason="unsupported launch")
        if request.resume_session_id is not None:
            window_id = self.terminal.window_for_session(request.resume_session_id)
            if window_id is not None:
                return LaunchResult("rejected", reason="session is already live")
        try:
            plan = plugin.launcher.prepare(request)
        except LaunchRejected as error:
            return LaunchResult("rejected", reason=str(error))
        terminal_result = self.terminal.open_tab(
            TabRequest(
                working_directory=request.working_directory,
                command=(plan.command, *plan.arguments),
                title=plan.title,
            )
        )
        if not terminal_result.succeeded:
            return LaunchResult("rejected", reason=terminal_result.reason)
        if terminal_result.window_id is None:
            return LaunchResult("rejected", reason="terminal did not identify the launched window")
        return LaunchResult("started", window_id=terminal_result.window_id)


class HarnessCatalogService:
    def __init__(self, registry: HarnessRegistry) -> None:
        self.registry = registry

    def read(self, harness: str, context: QueryContext) -> HarnessCatalogSnapshot:
        catalog = self.registry.plugin(harness).catalog
        if catalog is None:
            raise ValueError(f"harness {harness!r} has no catalog")
        return catalog.read(context)

class HarnessUsageService(UsageSource):
    def __init__(self, registry: HarnessRegistry) -> None:
        self.registry = registry

    def read(self) -> tuple[UsageRow, ...]:
        rows = []
        for plugin in self.registry.plugins():
            if plugin.usage is None:
                continue
            plugin_rows = plugin.usage.read()
            if any(row.harness != plugin.info.name for row in plugin_rows):
                raise ValueError("usage row harness does not match its plugin")
            rows.extend(plugin_rows)
        return tuple(rows)


class TerminalInputService:
    def __init__(
        self,
        registry: HarnessRegistry,
        terminal: SessionTerminal,
        screen: TerminalScreen,
    ) -> None:
        self.registry = registry
        self.terminal = terminal
        self.screen = screen

    def read(self, session_id: SessionId) -> TerminalInputState | None:
        return self.state(session_id).input_state

    def state(self, session_id: SessionId) -> TerminalSessionState:
        window_id = self.terminal.window_for_session(session_id)
        plugin = self.registry.plugin_for_session(session_id)
        input_state = (
            plugin.terminal_probe.input_state(self.screen, window_id)
            if window_id is not None and plugin.terminal_probe is not None
            else None
        )
        return TerminalSessionState(window_id, input_state)
