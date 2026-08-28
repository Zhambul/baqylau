"""Launch Claude Code and handle its pre-session screens."""

from __future__ import annotations

import time

from audit.models import HarnessStartupAudit
from audit.recorder import AuditRecorder
from domain.ids import HarnessName, WindowId
from harness.contract import HarnessLauncher, SessionResumeRecorder
from harness.impl.claude_code.attachments import prompt_with_attachments
from harness.models import LaunchRequest, LaunchResult, LaunchStatus
from harness.runtime import HarnessRuntimeConfig
from terminal.contract import TerminalPlugin
from terminal.launch import launch_tab_request
from terminal.models import KeySendRequest, ScreenReadRequest, SESSION_WINDOW_TAG
from terminal.models.values import WindowId as TerminalWindowId

# The launch selections ride the CLI environment. Claude Code does not report
# effort in its event data. It reports the model after the first response. The
# hook reads these values when the session starts.
LAUNCH_MODEL_VARIABLE = "BAQYLAU_LAUNCH_MODEL"
LAUNCH_EFFORT_VARIABLE = "BAQYLAU_LAUNCH_EFFORT"

POLL_SECONDS = 0.25
STARTUP_TIMEOUT_SECONDS = 30.0
SCREEN_LIMIT = 4_000
PERMISSION_ARGUMENT = "--dangerously-skip-permissions"


class ClaudeCodeLauncher(HarnessLauncher):
    def __init__(
        self,
        harness_runtime_config: HarnessRuntimeConfig,
        terminal_plugin: TerminalPlugin,
        session_resume_recorder: SessionResumeRecorder,
        audit_recorder: AuditRecorder,
        launch_environment: tuple[tuple[str, str], ...] = (),
    ) -> None:
        self.runtime = harness_runtime_config
        self.terminal = terminal_plugin
        self.launch_effects = session_resume_recorder
        self.audit = audit_recorder
        self.launch_environment = launch_environment

    def launch(self, launch_request: LaunchRequest) -> LaunchResult:
        if launch_request.account_id is not None:
            return LaunchResult(
                LaunchStatus.REJECTED,
                reason="Claude Code does not support account selection",
            )
        if self._already_live(launch_request):
            return LaunchResult(
                LaunchStatus.REJECTED,
                reason="session is already live",
            )

        prompt = prompt_with_attachments(
            launch_request.initial_text or "",
            launch_request.attachments,
        )
        arguments = [PERMISSION_ARGUMENT]
        environment = list(self.launch_environment)
        if not self.runtime.use_vendor_default_configuration:
            environment.append(
                ("CLAUDE_CONFIG_DIR", str(self.runtime.configuration_directory))
            )
        if self.runtime.settings_file is not None:
            environment.append(
                ("CLAUDE_CODE_MANAGED_SETTINGS_PATH", str(self.runtime.settings_file))
            )
        if launch_request.resume_session_id is not None:
            arguments.extend(("--resume", str(launch_request.resume_session_id)))
        if launch_request.model:
            arguments.extend(("--model", launch_request.model))
            environment.append((LAUNCH_MODEL_VARIABLE, launch_request.model))
        if launch_request.effort:
            arguments.extend(("--effort", launch_request.effort))
            environment.append((LAUNCH_EFFORT_VARIABLE, launch_request.effort))
        if prompt.strip():
            arguments.append(prompt)

        opened = self.terminal.tabs.open_tab(
            launch_tab_request(
                launch_request.working_directory,
                (self.runtime.executable, *arguments),
                title="Claude Code",
                environment=tuple(environment),
            )
        )
        if not opened.succeeded:
            return LaunchResult(LaunchStatus.REJECTED, reason=opened.reason)
        if opened.window_id is None:
            return LaunchResult(
                LaunchStatus.REJECTED,
                reason="terminal did not identify the launched window",
            )

        window_id = WindowId(str(opened.window_id))
        self._record(window_id, "opened", "terminal tab opened")
        if launch_request.resume_session_id is not None:
            self.launch_effects.resumed(
                HarnessName.CLAUDE_CODE,
                launch_request.resume_session_id,
                window_id,
            )

        handled_screens: set[str] = set()
        deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
        last_screen: str | None = None
        last_kind: str | None = None
        while time.monotonic() < deadline:
            if self._session_started(window_id):
                self._record(window_id, "ready", "session started")
                return LaunchResult(LaunchStatus.STARTED, window_id=window_id)

            read = self.terminal.viewport.read_screen(
                ScreenReadRequest(TerminalWindowId(str(window_id)))
            )
            if read.succeeded and read.text:
                last_screen = read.text
                if (
                    "Managed settings require approval" in read.text
                    and "Yes, I trust these settings" in read.text
                ):
                    last_kind = "managed_settings"
                    if read.text not in handled_screens:
                        sent = self.terminal.input.send_key(
                            KeySendRequest(TerminalWindowId(str(window_id)), "enter")
                        )
                        if not sent.succeeded:
                            message = "could not approve Claude Code managed settings"
                            self._record(
                                window_id,
                                "input_failed",
                                message,
                                last_kind,
                                read.text,
                            )
                            return LaunchResult(
                                LaunchStatus.REJECTED,
                                window_id,
                                message,
                            )
                        handled_screens.add(read.text)
                        self._record(
                            window_id,
                            "handled",
                            "approved Claude Code managed settings",
                            last_kind,
                            read.text,
                        )
                elif (
                    "Choose the text style that looks best with your terminal"
                    in read.text
                    and "To change this later, run /theme" in read.text
                ):
                    return self._error(
                        window_id,
                        "onboarding",
                        "Claude Code needs onboarding in the terminal tab",
                        read.text,
                    )
                elif "Select login method:" in read.text:
                    return self._error(
                        window_id,
                        "login",
                        "Claude Code needs you to sign in in the terminal tab",
                        read.text,
                    )
                elif (
                    "Do you trust the files in this folder?" in read.text
                    or "Do you trust this folder?" in read.text
                ):
                    last_kind = "workspace_trust"
                    if read.text not in handled_screens:
                        sent = self.terminal.input.send_key(
                            KeySendRequest(TerminalWindowId(str(window_id)), "enter")
                        )
                        if not sent.succeeded:
                            message = "could not approve the Claude Code workspace"
                            self._record(
                                window_id,
                                "input_failed",
                                message,
                                last_kind,
                                read.text,
                            )
                            return LaunchResult(
                                LaunchStatus.REJECTED,
                                window_id,
                                message,
                            )
                        handled_screens.add(read.text)
                        self._record(
                            window_id,
                            "handled",
                            "approved the Claude Code workspace",
                            last_kind,
                            read.text,
                        )
            time.sleep(POLL_SECONDS)

        if last_kind is not None:
            message = (
                "Claude Code did not continue after Baqylau handled the "
                f"{last_kind} screen"
            )
        else:
            message = "Claude Code did not start; check the terminal tab"
        return self._error(
            window_id,
            last_kind or "unrecognized",
            message,
            last_screen,
        )

    def _already_live(self, launch_request: LaunchRequest) -> bool:
        session_id = launch_request.resume_session_id
        return session_id is not None and any(
            window.tags.get(SESSION_WINDOW_TAG) == str(session_id)
            for window in self.terminal.metadata.windows()
        )

    def _session_started(self, window_id: WindowId) -> bool:
        return any(
            str(window.window_id) == str(window_id)
            and bool(window.tags.get(SESSION_WINDOW_TAG))
            for window in self.terminal.metadata.windows()
        )

    def _error(
        self,
        window_id: WindowId,
        screen_kind: str,
        message: str,
        screen: str | None,
    ) -> LaunchResult:
        self._record(
            window_id,
            "error",
            message,
            screen_kind,
            screen,
        )
        return LaunchResult(LaunchStatus.REJECTED, window_id, message)

    def _record(
        self,
        window_id: WindowId,
        outcome: str,
        message: str,
        screen_kind: str | None = None,
        screen: str | None = None,
    ) -> None:
        self.audit.state_file(
            "",
            str(window_id),
            "launch-startup",
            HarnessStartupAudit(
                harness=HarnessName.CLAUDE_CODE,
                window_id=window_id,
                screen_kind=screen_kind,
                outcome=outcome,
                message=message,
                screen=screen[-SCREEN_LIMIT:] if screen is not None else None,
            ),
        )
