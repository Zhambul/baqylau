# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide foundation test sources."""

from __future__ import annotations

from harness.models.translation_stages import TranslationStage
from tests import canonical_foundation_components as foundation_components, foundation_dependencies


class RecordingSnapshots:
    """Record terminal snapshot invalidations."""

    def __init__(self) -> None:
        """Start the invalidation counter at zero."""
        self.invalidations = 0

    def sample(
        self,
    ) -> tuple[foundation_components.terminal_value_models.WindowInfo, ...]:
        """Read the fixed terminal snapshot.

        Returns:
            An empty window collection.

        """
        return ()

    def invalidate(self) -> None:
        """Count a snapshot invalidation."""
        self.invalidations += 1


class BuggyTranslator:
    """Raise an unexpected failure for each translation."""

    def translate(
        self, raw_event: foundation_components.raw_events.RawEvent,
        *, translation_stage: TranslationStage = TranslationStage.COMPLETE,
    ) -> foundation_dependencies.standard.typing.Never:
        """Simulate a programming error during translation.

        Raises:
            ZeroDivisionError: For every raw event.

        """
        message = f"translator bug for {raw_event.raw_event_id} in {translation_stage.value}"
        raise ZeroDivisionError(message)

    def release_session(self, session_id: foundation_dependencies.domain.domain_ids.SessionId) -> None:
        """Record the released session identifier."""
        self.released_session_id = session_id


class BrokenSource:
    """Raise an unexpected failure for each source read."""

    source_identity = "broken"

    def watch_paths(self) -> tuple[str, ...]:
        """Return no file inputs for this failing source.

        Returns:
            No file inputs for this failing source.

        """
        return ()

    def read(self, after_position: str | None) -> foundation_dependencies.standard.typing.Never:
        """Simulate an unexpected source failure.

        Raises:
            RuntimeError: For every read position.

        """
        message = f"this source is broken after {after_position}"
        raise RuntimeError(message)


class SingleSessionLookup:
    """Return one session by its identifier."""

    def __init__(self, session: foundation_dependencies.engine.Session) -> None:
        """Store the session available to this lookup."""
        self._session = session

    def find(
        self, session_id: foundation_dependencies.domain.domain_ids.SessionId,
    ) -> foundation_dependencies.engine.Session | None:
        """Find the stored session by identifier.

        Returns:
            The session if its identifier matches, or None otherwise.

        """
        if session_id != self._session.session_id:
            return None
        return self._session


class NullTerminal:
    """Represent null terminal."""

    def __init__(self) -> None:
        """Initialize the terminal."""
        self.closed_sessions: list[foundation_dependencies.domain.domain_ids.SessionId] = []
        self.open_checks: list[foundation_dependencies.domain.domain_ids.SessionId] = []
        self.ownership_checks: list[tuple[foundation_dependencies.domain.domain_ids.WindowId, int | None, str]] = []

    def close_session_panes(
        self, session_id: foundation_dependencies.domain.domain_ids.SessionId,
    ) -> foundation_components.adapter.SessionTerminalResult:
        """Record a request to close the session panes.

        Returns:
            A successful terminal result.

        """
        self.closed_sessions.append(session_id)
        return foundation_components.adapter.SessionTerminalResult(succeeded=True)

    def session_panes_are_open(self, session_id: foundation_dependencies.domain.domain_ids.SessionId) -> bool:
        """Record a check for open session panes.

        Returns:
            True for every session.

        """
        self.open_checks.append(session_id)
        return True

    def open_session_panes(
        self, request: foundation_components.adapter.SessionPaneRequest,
    ) -> foundation_dependencies.standard.typing.Never:
        """Reject attempts to open panes that the test reports as open.

        Raises:
            AssertionError: For every open request.

        """
        message = f"panes must not open for session {request.session_id} when they are already open"
        raise AssertionError(message)

    def window_hosts_process(
        self, window_id: foundation_dependencies.domain.domain_ids.WindowId, process_id: int | None, process_name: str,
    ) -> bool:
        """Record a process ownership check.

        Returns:
            False for every window and process.

        """
        self.ownership_checks.append((window_id, process_id, process_name))
        return False


class NullLauncher:
    """A launch capability used only to mark a plug-in as launchable."""

    def __init__(self) -> None:
        """Initialize the launcher."""
        self.requests: list[foundation_components.launch.LaunchRequest] = []

    def launch(
        self, launch_request: foundation_components.launch.LaunchRequest,
    ) -> foundation_components.launch.LaunchResult:
        """Reject the unused launch request.

        Returns:
            A rejected launch result with the test reason.

        """
        self.requests.append(launch_request)
        return foundation_components.launch.LaunchResult(
            foundation_components.launch.LaunchStatus.REJECTED, reason="not used",
        )


class NullControls:
    """Represent null controls."""

    def execute(
        self, request: foundation_dependencies.engine.ControlRequest,
    ) -> foundation_dependencies.standard.typing.Never:
        """Reject unexpected control requests.

        Raises:
            AssertionError: For every control request.

        """
        message = f"unexpected control: {request}"
        raise AssertionError(message)
