"""Starting a harness CLI in a new terminal tab."""

from __future__ import annotations

from domain.ids import HarnessName, WindowId
from harness.models import LaunchRejected, LaunchRequest, LaunchResult
from harness.models.launch import LaunchStatus
from harness.registry import HarnessRegistry
from harness.contract import SessionResumeRecorder
from terminal.adapter import TerminalAdapter
from terminal.contract import TerminalTabs
from terminal.launch import launch_tab_request


class HarnessLauncherService:
    def __init__(
        self,
        harness_registry: HarnessRegistry,
        terminal_adapter: TerminalAdapter,
        terminal_tabs: TerminalTabs,
        session_resume_recorder: SessionResumeRecorder,
        launch_environment: tuple[tuple[str, str], ...] = (),
    ) -> None:
        self.registry = harness_registry
        self.terminal = terminal_adapter
        self.tabs = terminal_tabs
        self.launch_effects = session_resume_recorder
        self.launch_environment = launch_environment

    def launch(self, harness: HarnessName, launch_request: LaunchRequest) -> LaunchResult:
        plugin = self.registry.plugin(harness)
        if plugin.launcher is None:
            return LaunchResult(LaunchStatus.REJECTED, reason="unsupported launch")
        # The one door every launch route comes through, so the one place the
        # "announces itself only at the first turn" harnesses are held to a first
        # message (HarnessInfo.requires_initial_message). Declined here rather
        # than in the harness's own launcher: the rule is about what OUR
        # observation needs, not about the argv the harness builds.
        if plugin.info.requires_initial_message and not launch_request.carries_first_message:
            return LaunchResult(
                LaunchStatus.REJECTED,
                reason=(f"{plugin.info.display_name} needs a first message — it appears here only once one is sent"),
            )
        if launch_request.resume_session_id is not None:
            window_id = self.terminal.window_for_session(launch_request.resume_session_id)
            if window_id is not None:
                return LaunchResult(LaunchStatus.REJECTED, reason="session is already live")
        try:
            plan = plugin.launcher.prepare(launch_request)
        except LaunchRejected as error:
            return LaunchResult(LaunchStatus.REJECTED, reason=str(error))
        terminal_result = self.tabs.open_tab(
            launch_tab_request(
                launch_request.working_directory,
                (plan.command, *plan.arguments),
                title=plan.title,
                environment=(*self.launch_environment, *plan.environment),
            )
        )
        if not terminal_result.succeeded:
            return LaunchResult(LaunchStatus.REJECTED, reason=terminal_result.reason)
        if terminal_result.window_id is None:
            return LaunchResult(LaunchStatus.REJECTED, reason="terminal did not identify the launched window")
        if launch_request.resume_session_id is not None:
            self.launch_effects.resumed(
                harness,
                launch_request.resume_session_id,
                WindowId(str(terminal_result.window_id)),
            )
        return LaunchResult(LaunchStatus.STARTED, window_id=WindowId(str(terminal_result.window_id)))
