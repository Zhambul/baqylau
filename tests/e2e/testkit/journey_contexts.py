# Copyright (c) 2026 Zhambyl Yermagambet
"""Contain focused dependencies for journey steps."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from terminal.models.tabs import EnvironmentVariable
from tests.e2e.testkit import references as refs
from tests.e2e.testkit.journeys import JourneyDriver


@dataclass(frozen=True)
class JourneyStartContext:
    """Contain services that start journey sessions."""

    driver: JourneyDriver
    session_specs: refs.SessionSpecs
    journeys: refs.SessionJourneys
    sessions: refs.Sessions
    turns: refs.Turns


@dataclass(frozen=True)
class JourneyContinueContext:
    """Contain services that continue journey sessions."""

    driver: JourneyDriver
    journeys: refs.SessionJourneys
    turns: refs.Turns


@dataclass(frozen=True)
class JourneyResumeContext:
    """Contain services that resume journey sessions."""

    driver: JourneyDriver
    journeys: refs.SessionJourneys
    continuations: refs.SessionContinuations
    sessions: refs.Sessions
    turns: refs.Turns


@dataclass(frozen=True)
class IsolatedHarnessHomes:
    """Contain isolated harness configuration directories."""

    codex: Path
    claude: Path
    state: Path

    def launch_environment(self) -> tuple[EnvironmentVariable, ...]:
        """Build the environment for a session with isolated harness files.

        Returns:
            The Codex home, Claude home, Claude managed settings, and state paths.

        """
        return (
            EnvironmentVariable("CODEX_HOME", str(self.codex)),
            EnvironmentVariable("CLAUDE_CONFIG_DIR", str(self.claude)),
            EnvironmentVariable("CLAUDE_CODE_MANAGED_SETTINGS_PATH", str(self.claude / "managed-settings.json")),
            # OpenCode2 keeps the effort of a new session in the state home.
            EnvironmentVariable("XDG_STATE_HOME", str(self.state)),
        )
