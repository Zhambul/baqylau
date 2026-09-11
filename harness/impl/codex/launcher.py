# Copyright (c) 2026 Zhambyl Yermagambet
"""Launch Codex and report screens that need the user."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from harness.impl.codex import launcher_dependencies as dependencies

if TYPE_CHECKING:
    from audit.recorder import AuditRecorder
    from terminal.contract import TerminalPlugin

POLL_SECONDS = 0.25
STARTUP_TIMEOUT_SECONDS = 30.0
SCREEN_LIMIT = 4_000


@dataclass
class _StartupState:
    deadline: float
    handled_screens: set[str] = field(default_factory=set)
    last_screen: str | None = None


def _codex_prompt(launch_request: dependencies.launch.LaunchRequest) -> str:
    attachment_text = " ".join(attachment.local_path for attachment in launch_request.attachments)
    initial_text = launch_request.initial_text or ""
    if attachment_text and initial_text:
        return f"{attachment_text}\n{initial_text}"
    return attachment_text or initial_text


def _codex_arguments(launch_request: dependencies.launch.LaunchRequest) -> list[str]:
    arguments: list[str] = []
    if launch_request.resume_session_id is not None:
        arguments.extend(("resume", str(launch_request.resume_session_id)))
    if launch_request.working_directory:
        arguments.extend(("-C", launch_request.working_directory))
    if launch_request.model:
        arguments.extend(("-m", launch_request.model))
    if launch_request.effort:
        arguments.extend(("-c", f"model_reasoning_effort={launch_request.effort}"))
    arguments.extend(("-c", 'model_reasoning_summary="concise"'))
    prompt = _codex_prompt(launch_request)
    if prompt.strip():
        arguments.append(prompt)
    return arguments


def _launch_rejection(launch_request: dependencies.launch.LaunchRequest, *, already_live: bool) -> str | None:
    if launch_request.account_id is not None:
        return "Codex has no account switcher"
    if not launch_request.carries_first_message:
        return "Codex needs a first message because its session starts when it receives the message"
    if already_live:
        return "session is already live"
    return None


def _already_live(terminal_plugin: TerminalPlugin, launch_request: dependencies.launch.LaunchRequest) -> bool:
    session_id = launch_request.resume_session_id
    if session_id is None:
        return False
    return any(
        window.tags.get(dependencies.SESSION_WINDOW_TAG) == str(session_id)
        for window in terminal_plugin.metadata.windows()
    )


def _session_started(terminal_plugin: TerminalPlugin, window_id: dependencies.ids.WindowId) -> bool:
    for window in terminal_plugin.metadata.windows():
        if str(window.window_id) != str(window_id):
            continue
        return bool(window.tags.get(dependencies.SESSION_WINDOW_TAG))
    return False


class CodexLauncher(dependencies.contract.HarnessLauncher):
    """Represent codex launcher."""

    def __init__(
        self,
        harness_runtime_config: dependencies.runtime.HarnessRuntimeConfig,
        terminal_plugin: TerminalPlugin,
        session_resume_recorder: dependencies.contract.SessionResumeRecorder,
        audit_recorder: AuditRecorder,
        launch_environment: tuple[dependencies.tabs.EnvironmentVariable, ...] = (),
    ) -> None:
        """Initialize the object."""
        self.runtime = harness_runtime_config
        self.terminal = terminal_plugin
        self.launch_effects = session_resume_recorder
        self.audit = audit_recorder
        self.launch_environment = launch_environment

    def launch(self, launch_request: dependencies.launch.LaunchRequest) -> dependencies.launch.LaunchResult:
        """Launch launch.

        Returns:
            The launch result.

        """
        rejection = _launch_rejection(
            launch_request,
            already_live=_already_live(self.terminal, launch_request),
        )
        if rejection is not None:
            return dependencies.launch.LaunchResult(dependencies.launch.LaunchStatus.REJECTED, reason=rejection)
        opened = self.terminal.tabs.open_tab(
            dependencies.launch_tab_request(
                launch_request.working_directory,
                (self.runtime.executable, *_codex_arguments(launch_request)),
                title="Codex",
                environment=(
                    *self.launch_environment,
                    dependencies.tabs.EnvironmentVariable(
                        "CODEX_HOME",
                        str(self.runtime.configuration_directory),
                    ),
                ),
            ),
        )
        if not opened.succeeded:
            return dependencies.launch.LaunchResult(dependencies.launch.LaunchStatus.REJECTED, reason=opened.reason)
        if opened.window_id is None:
            return dependencies.launch.LaunchResult(
                dependencies.launch.LaunchStatus.REJECTED,
                reason="terminal did not identify the launched window",
            )

        window_id = dependencies.ids.WindowId(str(opened.window_id))
        self._record(window_id, "opened", "terminal tab opened")
        if launch_request.resume_session_id is not None:
            self.launch_effects.resumed(
                dependencies.ids.HarnessName("codex"),
                launch_request.resume_session_id,
                window_id,
            )
        return self._wait_for_start(window_id)

    def _wait_for_start(self, window_id: dependencies.ids.WindowId) -> dependencies.launch.LaunchResult:
        state = _StartupState(time.monotonic() + STARTUP_TIMEOUT_SECONDS)
        while time.monotonic() < state.deadline:
            if _session_started(self.terminal, window_id):
                self._record(window_id, "ready", "session started")
                return dependencies.launch.LaunchResult(dependencies.launch.LaunchStatus.STARTED, window_id=window_id)
            screen_result = self._read_startup_screen(window_id, state)
            if screen_result is not None:
                return screen_result
            time.sleep(POLL_SECONDS)
        return self._error(
            window_id,
            "unrecognized",
            "Codex did not start; check the terminal tab",
            state.last_screen,
        )

    def _read_startup_screen(
        self,
        window_id: dependencies.ids.WindowId,
        startup_state: _StartupState,
    ) -> dependencies.launch.LaunchResult | None:
        read = self.terminal.viewport.read_screen(
            dependencies.viewport.ScreenReadRequest(dependencies.WindowId(str(window_id))),
        )
        if not read.succeeded or not read.text:
            return None
        startup_state.last_screen = read.text
        if "Welcome to Codex, OpenAI's command-line coding agent" in read.text and "Sign in with ChatGPT" in read.text:
            return self._error(
                window_id,
                "login",
                "Codex needs you to sign in in the terminal tab",
                read.text,
            )
        if "Do you trust the contents of this directory?" in read.text or "Do you trust this directory?" in read.text:
            return self._approve_workspace(window_id, startup_state, read.text)
        if ">_ OpenAI Codex" in read.text:
            accepted = "\u203a Ask Codex to do anything" in read.text or "esc to interrupt" in read.text
            if accepted:
                self._record(window_id, "ready", "Codex accepted the launch", "main")
                return dependencies.launch.LaunchResult(dependencies.launch.LaunchStatus.STARTED, window_id=window_id)
        return None

    def _approve_workspace(
        self,
        window_id: dependencies.ids.WindowId,
        startup_state: _StartupState,
        screen: str,
    ) -> dependencies.launch.LaunchResult | None:
        if screen in startup_state.handled_screens:
            return None
        sent = self.terminal.input.send_key(
            dependencies.input_models.KeySendRequest(dependencies.WindowId(str(window_id)), "enter"),
        )
        if not sent.succeeded:
            return self._error(
                window_id,
                "workspace_trust",
                "could not approve the Codex workspace",
                screen,
            )
        startup_state.handled_screens.add(screen)
        self._record(window_id, "handled", "approved the Codex workspace", "workspace_trust", screen)
        return None

    def _error(
        self,
        window_id: dependencies.ids.WindowId,
        screen_kind: str,
        message: str,
        screen: str | None,
    ) -> dependencies.launch.LaunchResult:
        self._record(
            window_id,
            "error",
            message,
            screen_kind,
            screen,
        )
        return dependencies.launch.LaunchResult(dependencies.launch.LaunchStatus.REJECTED, window_id, message)

    def _record(
        self,
        window_id: dependencies.ids.WindowId,
        outcome: str,
        message: str,
        screen_kind: str | None = None,
        screen: str | None = None,
    ) -> None:
        self.audit.state_file(
            "",
            str(window_id),
            "launch-startup",
            dependencies.HarnessStartupAudit(
                harness=dependencies.ids.HarnessName("codex"),
                window_id=window_id,
                screen_kind=screen_kind,
                outcome=outcome,
                message=message,
                screen=None if screen is None else screen[-SCREEN_LIMIT:],
            ),
        )
