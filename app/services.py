"""Application controls and native live-state reads routed by persisted ownership."""

from __future__ import annotations

from typing import Protocol

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

class HarnessUsageService:
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
