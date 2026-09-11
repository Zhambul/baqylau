# Copyright (c) 2026 Zhambyl Yermagambet
"""Launch Claude Code and handle its pre-session screens."""

from __future__ import annotations

from typing import TYPE_CHECKING

from domain.ids import HarnessName, WindowId
from harness.contract import HarnessLauncher, SessionResumeRecorder
from harness.impl.claude_code import startup
from harness.impl.claude_code.attachments import prompt_with_attachments
from harness.models.launch import (
    LaunchRequest,
    LaunchResult,
    LaunchStatus,
)
from terminal.launch import launch_tab_request
from terminal.models.tabs import (
    EnvironmentVariable,
)

if TYPE_CHECKING:
    from audit.recorder import AuditRecorder
    from harness.runtime import HarnessRuntimeConfig
    from terminal.contract import TerminalPlugin

# The launch selections ride the CLI environment. Claude Code does not report
# effort in its event data. It reports the model after the first response. The
# hook reads these values when the session starts.
LAUNCH_MODEL_VARIABLE = "BAQYLAU_LAUNCH_MODEL"
LAUNCH_EFFORT_VARIABLE = "BAQYLAU_LAUNCH_EFFORT"

PERMISSION_ARGUMENT = "--dangerously-skip-permissions"


def _claude_arguments(launch_request: LaunchRequest) -> list[str]:
    arguments = [PERMISSION_ARGUMENT]
    if launch_request.resume_session_id is not None:
        arguments.extend(("--resume", str(launch_request.resume_session_id)))
    if launch_request.model:
        arguments.extend(("--model", launch_request.model))
    if launch_request.effort:
        arguments.extend(("--effort", launch_request.effort))
    prompt = prompt_with_attachments(
        launch_request.initial_text or "",
        launch_request.attachments,
    )
    if prompt.strip():
        arguments.append(prompt)
    return arguments


def _claude_environment(
    launch_request: LaunchRequest,
    harness_runtime_config: HarnessRuntimeConfig,
    launch_environment: tuple[EnvironmentVariable, ...],
) -> tuple[EnvironmentVariable, ...]:
    environment = list(launch_environment)
    if not harness_runtime_config.use_vendor_default_configuration:
        environment.append(
            EnvironmentVariable("CLAUDE_CONFIG_DIR", str(harness_runtime_config.configuration_directory)),
        )
    if harness_runtime_config.settings_file is not None:
        environment.append(
            EnvironmentVariable("CLAUDE_CODE_MANAGED_SETTINGS_PATH", str(harness_runtime_config.settings_file)),
        )
    if launch_request.model:
        environment.append(EnvironmentVariable(LAUNCH_MODEL_VARIABLE, launch_request.model))
    if launch_request.effort:
        environment.append(EnvironmentVariable(LAUNCH_EFFORT_VARIABLE, launch_request.effort))
    return tuple(environment)


def _launch_rejection(launch_request: LaunchRequest, *, already_live: bool) -> str | None:
    if launch_request.account_id is not None:
        return "Claude Code does not support account selection"
    if already_live:
        return "session is already live"
    return None


class ClaudeCodeLauncher(HarnessLauncher):
    """Represent claude code launcher."""

    def __init__(
        self,
        harness_runtime_config: HarnessRuntimeConfig,
        terminal_plugin: TerminalPlugin,
        session_resume_recorder: SessionResumeRecorder,
        audit_recorder: AuditRecorder,
        launch_environment: tuple[EnvironmentVariable, ...] = (),
    ) -> None:
        """Initialize the object."""
        self.runtime = harness_runtime_config
        self.terminal = terminal_plugin
        self.launch_effects = session_resume_recorder
        self.audit = audit_recorder
        self.launch_environment = launch_environment
        self.startup = startup.ClaudeStartupMonitor(terminal_plugin, audit_recorder)

    def launch(self, launch_request: LaunchRequest) -> LaunchResult:
        """Launch launch.

        Returns:
            The launch result.

        """
        rejection = _launch_rejection(
            launch_request,
            already_live=self._already_live(launch_request),
        )
        if rejection is not None:
            return LaunchResult(LaunchStatus.REJECTED, reason=rejection)
        opened = self.terminal.tabs.open_tab(
            launch_tab_request(
                launch_request.working_directory,
                (self.runtime.executable, *_claude_arguments(launch_request)),
                title="Claude Code",
                environment=_claude_environment(
                    launch_request,
                    self.runtime,
                    self.launch_environment,
                ),
            ),
        )
        if not opened.succeeded:
            return LaunchResult(LaunchStatus.REJECTED, reason=opened.reason)
        if opened.window_id is None:
            return LaunchResult(
                LaunchStatus.REJECTED,
                reason="terminal did not identify the launched window",
            )

        window_id = WindowId(str(opened.window_id))
        startup.record_startup_state(
            self.audit,
            window_id,
            startup.StartupAuditRecord("opened", "terminal tab opened"),
        )
        if launch_request.resume_session_id is not None:
            self.launch_effects.resumed(
                HarnessName("claude_code"),
                launch_request.resume_session_id,
                window_id,
            )
        return self.startup.wait(window_id)

    def _already_live(self, launch_request: LaunchRequest) -> bool:
        session_id = launch_request.resume_session_id
        if session_id is None:
            return False
        return startup.session_is_live(self.terminal, session_id)
