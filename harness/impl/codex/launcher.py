"""Launch Codex and report screens that need the user."""

from __future__ import annotations

import time

from audit.models import HarnessStartupAudit
from audit.recorder import AuditRecorder
from domain.ids import HarnessName, WindowId
from harness.contract import HarnessLauncher, SessionResumeRecorder
from harness.models import LaunchRequest, LaunchResult, LaunchStatus
from harness.runtime import HarnessRuntimeConfig
from terminal.contract import TerminalPlugin
from terminal.launch import launch_tab_request
from terminal.models import KeySendRequest, ScreenReadRequest, SESSION_WINDOW_TAG
from terminal.models.values import WindowId as TerminalWindowId

POLL_SECONDS = 0.25
STARTUP_TIMEOUT_SECONDS = 30.0
SCREEN_LIMIT = 4_000


class CodexLauncher(HarnessLauncher):
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
                reason="Codex has no account switcher",
            )
        if not launch_request.carries_first_message:
            return LaunchResult(
                LaunchStatus.REJECTED,
                reason=(
                    "Codex needs a first message because its session starts "
                    "when it receives the message"
                ),
            )
        if self._already_live(launch_request):
            return LaunchResult(
                LaunchStatus.REJECTED,
                reason="session is already live",
            )

        attachment_text = " ".join(
            attachment.local_path for attachment in launch_request.attachments
        )
        initial_text = launch_request.initial_text or ""
        prompt = attachment_text + (
            "\n" + initial_text if attachment_text and initial_text else initial_text
        )
        arguments: list[str] = []
        if launch_request.resume_session_id is not None:
            arguments.extend(("resume", str(launch_request.resume_session_id)))
        if launch_request.working_directory:
            arguments.extend(("-C", launch_request.working_directory))
        if launch_request.model:
            arguments.extend(("-m", launch_request.model))
        if launch_request.effort:
            arguments.extend(
                ("-c", f"model_reasoning_effort={launch_request.effort}")
            )
        # Ask Codex to provide its own short readable reasoning summary.
        arguments.extend(("-c", 'model_reasoning_summary="concise"'))
        if prompt.strip():
            arguments.append(prompt)

        opened = self.terminal.tabs.open_tab(
            launch_tab_request(
                launch_request.working_directory,
                (self.runtime.executable, *arguments),
                title="Codex",
                environment=(
                    *self.launch_environment,
                    ("CODEX_HOME", str(self.runtime.configuration_directory)),
                ),
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
                HarnessName.CODEX,
                launch_request.resume_session_id,
                window_id,
            )

        handled_screens: set[str] = set()
        deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
        last_screen: str | None = None
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
                    "Welcome to Codex, OpenAI's command-line coding agent"
                    in read.text
                    and "Sign in with ChatGPT" in read.text
                ):
                    return self._error(
                        window_id,
                        "login",
                        "Codex needs you to sign in in the terminal tab",
                        read.text,
                    )
                if (
                    "Do you trust the contents of this directory?" in read.text
                    or "Do you trust this directory?" in read.text
                ) and read.text not in handled_screens:
                    sent = self.terminal.input.send_key(
                        KeySendRequest(TerminalWindowId(str(window_id)), "enter")
                    )
                    if not sent.succeeded:
                        return self._error(
                            window_id,
                            "workspace_trust",
                            "could not approve the Codex workspace",
                            read.text,
                        )
                    handled_screens.add(read.text)
                    self._record(
                        window_id,
                        "handled",
                        "approved the Codex workspace",
                        "workspace_trust",
                        read.text,
                    )
            time.sleep(POLL_SECONDS)

        return self._error(
            window_id,
            "unrecognized",
            "Codex did not start; check the terminal tab",
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
                harness=HarnessName.CODEX,
                window_id=window_id,
                screen_kind=screen_kind,
                outcome=outcome,
                message=message,
                screen=screen[-SCREEN_LIMIT:] if screen is not None else None,
            ),
        )
