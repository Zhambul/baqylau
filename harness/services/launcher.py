"""Starting a harness CLI in a new terminal tab."""

from __future__ import annotations

from harness.models import LaunchRejected, LaunchRequest, LaunchResult
from harness.registry import HarnessRegistry
from terminal.adapter import TerminalAdapter
from terminal.contract import TerminalTabs
from terminal.launch import launch_tab_request


class HarnessLauncherService:
    def __init__(
        self,
        registry: HarnessRegistry,
        terminal: TerminalAdapter,
        tabs: TerminalTabs,
    ) -> None:
        self.registry = registry
        self.terminal = terminal
        self.tabs = tabs

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
        terminal_result = self.tabs.open_tab(launch_tab_request(
            request.working_directory,
            (plan.command, *plan.arguments),
            title=plan.title,
            environment=plan.environment,
        ))
        if not terminal_result.succeeded:
            return LaunchResult("rejected", reason=terminal_result.reason)
        if terminal_result.window_id is None:
            return LaunchResult("rejected", reason="terminal did not identify the launched window")
        return LaunchResult("started", window_id=terminal_result.window_id)
