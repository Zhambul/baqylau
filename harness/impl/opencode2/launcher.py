# Copyright (c) 2026 Zhambyl Yermagambet
"""Launch the native OpenCode2 terminal with its event plugin."""

from dataclasses import dataclass, replace

from domain.ids import WindowId
from harness.contract import HarnessLauncher
from harness.impl.opencode2 import attachments, launch_config, launch_startup, launch_variant
from harness.impl.opencode2.plugin_info import DEFAULT_MODEL_ID
from harness.models.launch import LaunchRequest, LaunchResult, LaunchStatus
from harness.runtime import HarnessRuntimeConfig
from terminal.contract import TerminalPlugin
from terminal.launch import launch_tab_request
from terminal.models.tabs import EnvironmentVariable

DEFAULT_PORT = "8377"


@dataclass
class OpenCodeLauncher(HarnessLauncher):
    """Keep native launch settings beside the plugin."""

    runtime: HarnessRuntimeConfig
    terminal: TerminalPlugin
    environment: tuple[EnvironmentVariable, ...] = ()

    def launch(self, launch_request: LaunchRequest) -> LaunchResult:
        """Open the native interactive session.

        Returns:
            The opened window or an explicit rejection.

        """
        if launch_request.account_id is not None:
            return LaunchResult(
                LaunchStatus.REJECTED, reason="OpenCode2 account selection is not supported",
            )
        launch_request = replace(launch_request, initial_text=attachments.prompt(
            launch_request.initial_text, launch_request.attachments,
        ))
        environment = self._session_environment(launch_request)
        self._select_effort(launch_request, environment)
        opened = self.terminal.tabs.open_tab(launch_tab_request(
            launch_request.working_directory,
            (self.runtime.executable, *self._arguments(launch_request)),
            title="OpenCode2",
            environment=environment,
        ))
        if not opened.succeeded or opened.window_id is None:
            return LaunchResult(LaunchStatus.REJECTED, reason=opened.reason)
        # Only a NEW session keeps the first text in its draft: the start page
        # holds it until someone sends it. A RESUMED session sends that text
        # itself and draws no start page, so a draft sent here would wait for a
        # page that never comes.
        if launch_request.initial_text and launch_request.resume_session_id is None:
            reason = launch_startup.submit(self.terminal, opened.window_id, launch_request.initial_text)
            if reason is not None:
                return LaunchResult(LaunchStatus.REJECTED, reason=reason)
        return LaunchResult(LaunchStatus.STARTED, window_id=WindowId(str(opened.window_id)))

    def _session_environment(self, launch_request: LaunchRequest) -> tuple[EnvironmentVariable, ...]:
        return (*self.environment, *self._settings(), EnvironmentVariable(
            "OPENCODE_CONFIG_CONTENT", self._configuration(launch_request),
        ), EnvironmentVariable(
            "BAQYLAU_OPENCODE_LOG_DIR", str(self.runtime.configuration_directory),
        ), EnvironmentVariable("PWD", launch_request.working_directory))

    def _select_effort(
        self, launch_request: LaunchRequest, environment: tuple[EnvironmentVariable, ...],
    ) -> None:
        """Record the effort a NEW session starts with.

        The native default has priority over the launch configuration, so the
        effort is written where OpenCode2 reads it. That place comes from the
        environment of the new session, so the session that reads the effort and
        this launch that writes it always agree. A resumed session keeps its own
        effort and is left alone.
        """
        if launch_request.resume_session_id is not None or not launch_request.effort:
            return
        launch_variant.apply(environment, launch_request.model or DEFAULT_MODEL_ID, launch_request.effort)

    def _settings(self) -> tuple[EnvironmentVariable, ...]:
        if self.runtime.settings_file is None:
            return ()
        return (EnvironmentVariable("OPENCODE_CONFIG", str(self.runtime.settings_file)),)

    def _arguments(self, launch_request: LaunchRequest) -> tuple[str, ...]:
        arguments = ["--standalone"]
        if launch_request.resume_session_id is not None:
            arguments.extend(("--session", launch_request.resume_session_id))
        if launch_request.initial_text:
            arguments.extend(("--prompt", launch_request.initial_text))
        return tuple(arguments)

    def _configuration(self, launch_request: LaunchRequest) -> str:
        ports = (setting.content for setting in self.environment if setting.name == "BAQYLAU_DASHBOARD_PORT")
        model = launch_request.model or DEFAULT_MODEL_ID
        if launch_request.effort:
            model = f"{model}#{launch_request.effort}"
        return launch_config.session_configuration(
            model, str(self.runtime.configuration_directory), next(ports, DEFAULT_PORT),
        )
