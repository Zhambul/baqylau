# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide foundation test primitives."""

from __future__ import annotations

from harness.models import translation_stages as stages
from tests import canonical_foundation_components as foundation_components, foundation_dependencies

MAIN_DATABASE_NAME = "main.db"
type SourcePositions = foundation_dependencies.standard.Mapping[str, str]
type LatestPositionsReader = foundation_dependencies.standard.Callable[
    [foundation_dependencies.standard.Sequence[str]], SourcePositions,
]
FIXTURE_SOURCE_IDENTITY = "fixture:source"


@foundation_dependencies.standard.pytest.fixture
def database_path(tmp_path: foundation_dependencies.standard.Path) -> str:
    """Return the path of one isolated main database.

    Returns:
        The path of one isolated main database.

    """
    return str(tmp_path / MAIN_DATABASE_NAME)


def record_latest_positions(
    latest_positions_calls: list[tuple[str, ...]],
    native_latest_positions: LatestPositionsReader,
    source_identities: foundation_dependencies.standard.Sequence[str],
) -> foundation_dependencies.standard.Mapping[str, str]:
    """Record source identifiers before reading their latest positions.

    Returns:
        The positions returned by the supplied reader.

    """
    latest_positions_calls.append(tuple(source_identities))
    return native_latest_positions(source_identities)


def record_process_name_check(name_checks: list[int], process_id: int, _process_name: str) -> bool:
    """Record a process name check.

    Returns:
        True for every process identifier.

    """
    name_checks.append(process_id)
    return True


class FixedTranslator:
    """Represent fixed translator."""

    def __init__(
        self,
        translation: foundation_components.raw_events.TranslationResult
        | foundation_components.raw_events.TranslationError,
    ) -> None:
        """Store one fixed translation result."""
        self.translation = translation
        self.released: list[foundation_dependencies.domain.domain_ids.SessionId] = []
        self.raw_events: list[foundation_components.raw_events.RawEvent] = []

    def translate(
        self, raw_event: foundation_components.raw_events.RawEvent,
        *, translation_stage: stages.TranslationStage = stages.TranslationStage.COMPLETE,
    ) -> foundation_components.raw_events.TranslationResult:
        """Record an input event and return the fixed translation.

        A configured translation error is raised instead of returned.

        Returns:
            The configured translation result.

        """
        self.raw_events.append(raw_event)
        if isinstance(self.translation, foundation_components.raw_events.TranslationError):
            raise self.translation
        return stages.select_result(self.translation, translation_stage)

    def release_session(self, session_id: foundation_dependencies.domain.domain_ids.SessionId) -> None:
        """Process release session."""
        self.released.append(session_id)


class FixedSources:
    """Represent fixed sources."""

    def __init__(
        self, sources: tuple[foundation_dependencies.engine.harness_contract.HarnessRawEventSource, ...] = (),
    ) -> None:
        """Store fixed raw-event sources."""
        self.fixed = sources
        self.released: list[foundation_dependencies.domain.domain_ids.SessionId] = []
        self.sessions: list[foundation_dependencies.engine.Session] = []

    def for_session(
        self, session: foundation_dependencies.engine.Session,
    ) -> tuple[foundation_dependencies.engine.harness_contract.HarnessRawEventSource, ...]:
        """Record the session and return the fixed sources.

        Returns:
            The configured raw event sources.

        """
        self.sessions.append(session)
        return self.fixed

    def release_session(self, session_id: foundation_dependencies.domain.domain_ids.SessionId) -> None:
        """Process release session."""
        self.released.append(session_id)


class FixedReadSource:
    """Emits its raw events once; the recorded position latches it shut."""

    def __init__(
        self,
        raw_events: tuple[foundation_components.raw_events.RawEvent, ...],
        identity: str = FIXTURE_SOURCE_IDENTITY,
    ) -> None:
        """Store raw events for one source read."""
        self.raw_events = raw_events
        self.source_identity = identity

    def watch_paths(self) -> tuple[str, ...]:
        """Return no file inputs for this fixed source.

        Returns:
            No file inputs for this fixed source.

        """
        return ()

    def read(self, after_position: str | None) -> tuple[foundation_components.raw_events.RawEvent, ...]:
        """Return read.

        Returns:
            Read.

        """
        if after_position is not None:
            return ()
        return self.raw_events


class CountingTerminal:
    """Count terminal window reads."""

    def __init__(self) -> None:
        """Start the window read counter at zero."""
        self.calls = 0

    def windows(
        self,
    ) -> tuple[foundation_components.terminal_value_models.WindowInfo, ...]:
        """Count a window read.

        Returns:
            An empty window collection.

        """
        self.calls += 1
        return ()
