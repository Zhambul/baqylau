# Copyright (c) 2026 Zhambyl Yermagambet
"""Handle Claude Code screens before a session starts."""

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from audit.harness_documents import HarnessStartupAudit
from domain.ids import HarnessName, SessionId, WindowId
from harness.models.launch import LaunchResult, LaunchStatus
from terminal.models.input import KeySendRequest
from terminal.models.values import SESSION_WINDOW_TAG, WindowId as TerminalWindowId
from terminal.models.viewport import ScreenReadRequest

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
    last_kind: str | None = None


@dataclass(frozen=True)
class _ApprovalScreen:
    kind: str
    failure_message: str
    success_message: str


MANAGED_SETTINGS_APPROVAL = _ApprovalScreen(
    "managed_settings",
    "could not approve Claude Code managed settings",
    "approved Claude Code managed settings",
)
WORKSPACE_APPROVAL = _ApprovalScreen(
    "workspace_trust",
    "could not approve the Claude Code workspace",
    "approved the Claude Code workspace",
)


@dataclass(frozen=True)
class StartupAuditRecord:
    """Describe one startup state change."""

    outcome: str
    message: str
    screen_kind: str | None = None
    screen: str | None = None


def _session_started(terminal_plugin: "TerminalPlugin", window_id: WindowId) -> bool:
    for window in terminal_plugin.metadata.windows():
        if str(window.window_id) == str(window_id):
            return bool(window.tags.get(SESSION_WINDOW_TAG))
    return False


def session_is_live(terminal_plugin: "TerminalPlugin", session_id: SessionId) -> bool:
    """Return true if a terminal window has the session tag.

    Returns:
        True if the session has a live terminal window.

    """
    session_tag = str(session_id)
    window_session_tags = (window.tags.get(SESSION_WINDOW_TAG) for window in terminal_plugin.metadata.windows())
    return session_tag in window_session_tags


def record_startup_state(
    audit_recorder: "AuditRecorder",
    window_id: WindowId,
    startup_audit_record: StartupAuditRecord,
) -> None:
    """Write one startup state change to the audit log."""
    audit_recorder.state_file(
        "",
        str(window_id),
        "launch-startup",
        HarnessStartupAudit(
            harness=HarnessName("claude_code"),
            window_id=window_id,
            screen_kind=startup_audit_record.screen_kind,
            outcome=startup_audit_record.outcome,
            message=startup_audit_record.message,
            screen=None if startup_audit_record.screen is None else startup_audit_record.screen[-SCREEN_LIMIT:],
        ),
    )


class ClaudeStartupMonitor:
    """Handle known startup screens until the session starts."""

    def __init__(
        self,
        terminal_plugin: "TerminalPlugin",
        audit_recorder: "AuditRecorder",
    ) -> None:
        """Initialize the monitor."""
        self.terminal = terminal_plugin
        self.audit = audit_recorder

    def wait(self, window_id: WindowId) -> LaunchResult:
        """Wait for a session or a startup error.

        Returns:
            The launch result.

        """
        state = _StartupState(time.monotonic() + STARTUP_TIMEOUT_SECONDS)
        while time.monotonic() < state.deadline:
            if _session_started(self.terminal, window_id):
                record_startup_state(
                    self.audit,
                    window_id,
                    StartupAuditRecord("ready", "session started"),
                )
                return LaunchResult(LaunchStatus.STARTED, window_id=window_id)
            screen_result = self._read_startup_screen(window_id, state)
            if screen_result is not None:
                return screen_result
            time.sleep(POLL_SECONDS)
        return self._startup_timeout(window_id, state)

    def _read_startup_screen(
        self,
        window_id: WindowId,
        startup_state: _StartupState,
    ) -> LaunchResult | None:
        read = self.terminal.viewport.read_screen(
            ScreenReadRequest(TerminalWindowId(str(window_id))),
        )
        if not read.succeeded or not read.text:
            return None
        startup_state.last_screen = read.text
        if "Managed settings require approval" in read.text and "Yes, I trust these settings" in read.text:
            return self._approve_screen(window_id, startup_state, read.text, MANAGED_SETTINGS_APPROVAL)
        return self._read_startup_blocker(window_id, startup_state, read.text)

    def _read_startup_blocker(
        self,
        window_id: WindowId,
        startup_state: _StartupState,
        screen: str,
    ) -> LaunchResult | None:
        if (
            "Choose the text style that looks best with your terminal" in screen
            and "To change this later, run /theme" in screen
        ):
            return self._error(
                window_id,
                "onboarding",
                "Claude Code needs onboarding in the terminal tab",
                screen,
            )
        if "Select login method:" in screen:
            return self._error(
                window_id,
                "login",
                "Claude Code needs you to sign in in the terminal tab",
                screen,
            )
        workspace_trust = "Do you trust the files in this folder?" in screen or "Do you trust this folder?" in screen
        workspace_access = "Accessing workspace:" in screen and "Yes, I trust this folder" in screen
        if not workspace_trust and not workspace_access:
            return None
        if not workspace_trust and "\u276f No, exit" in screen:
            # The current screen selects No first. Select the named Yes row.
            sent = self.terminal.input.send_key(
                KeySendRequest(TerminalWindowId(str(window_id)), "down"),
            )
            if not sent.succeeded:
                return LaunchResult(LaunchStatus.REJECTED, window_id, WORKSPACE_APPROVAL.failure_message)
        return self._approve_screen(window_id, startup_state, screen, WORKSPACE_APPROVAL)

    def _approve_screen(
        self,
        window_id: WindowId,
        startup_state: _StartupState,
        screen: str,
        approval_screen: _ApprovalScreen,
    ) -> LaunchResult | None:
        startup_state.last_kind = approval_screen.kind
        if screen in startup_state.handled_screens:
            return None
        sent = self.terminal.input.send_key(
            KeySendRequest(TerminalWindowId(str(window_id)), "enter"),
        )
        if not sent.succeeded:
            record_startup_state(
                self.audit,
                window_id,
                StartupAuditRecord(
                    "input_failed",
                    approval_screen.failure_message,
                    approval_screen.kind,
                    screen,
                ),
            )
            return LaunchResult(LaunchStatus.REJECTED, window_id, approval_screen.failure_message)
        startup_state.handled_screens.add(screen)
        record_startup_state(
            self.audit,
            window_id,
            StartupAuditRecord(
                "handled",
                approval_screen.success_message,
                approval_screen.kind,
                screen,
            ),
        )
        return None

    def _startup_timeout(
        self,
        window_id: WindowId,
        startup_state: _StartupState,
    ) -> LaunchResult:
        message = (
            "Claude Code did not start; check the terminal tab"
            if startup_state.last_kind is None
            else f"Claude Code did not continue after Baqylau handled the {startup_state.last_kind} screen"
        )
        return self._error(
            window_id,
            startup_state.last_kind or "unrecognized",
            message,
            startup_state.last_screen,
        )

    def _error(
        self,
        window_id: WindowId,
        screen_kind: str,
        message: str,
        screen: str | None,
    ) -> LaunchResult:
        record_startup_state(
            self.audit,
            window_id,
            StartupAuditRecord("error", message, screen_kind, screen),
        )
        return LaunchResult(LaunchStatus.REJECTED, window_id, message)
