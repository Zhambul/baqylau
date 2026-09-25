# Copyright (c) 2026 Zhambyl Yermagambet
"""Canonical sessiondata api stream models."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Never

import domain as domain_modules
from dashboard.services import preference_models, workspace as workspace_models
from domain import (
    composer,
    ids as domain_ids,
)
from harness.models import probe as probe_models
from tests import canonical_sessiondata_api_values as api_values

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from audit.documents import AuditContent


class SilentAudit:
    """Represent silent audit."""

    def __init__(self) -> None:
        """Initialize the object."""
        self.failures: list[tuple[str, AuditContent]] = []
        self.sources: list[str] = []

    def error(
        self,
        session_or_log: str = "",
        func: str = "",
        context: AuditContent = None,
    ) -> None:
        """Process error."""
        self.sources.append(session_or_log)
        self.failures.append((func, context))


class ApplicationSnapshots:
    """Represent application snapshots."""

    def __init__(self) -> None:
        """Initialize the object."""
        self.enabled = True
        self.reads = 0

    def snapshot(self) -> preference_models.ApplicationPreferences:
        """Return snapshot.

        Returns:
            Snapshot.

        """
        self.reads += 1
        return preference_models.ApplicationPreferences(
            new_session=preference_models.NewSessionPreferences(None, None, None, None),
            new_session_drafts=(),
            hidden_directories={},
            limits=preference_models.DashboardLimits(
                1_000,
                api_values.MAXIMUM_TITLE_CHARACTERS,
                api_values.NOTIFICATION_SETTLE_SECONDS,
            ),
            notifications=preference_models.GlobalNotificationState(self.enabled, None),
            usage_rows=(),
        )


class SessionApplicationSnapshots:
    """Represent session application snapshots."""

    def snapshot(self, _session_id: domain_ids.SessionId) -> workspace_models.SessionApplicationSnapshot:
        """Return snapshot.

        Returns:
            Snapshot.

        """
        return workspace_models.SessionApplicationSnapshot(
            preferences=workspace_models.SessionPreferences(
                view_mode=domain_modules.preferences.DEFAULT_VIEW_MODE, notifications_muted=False, tasks_hidden=False,
            ),
            composer=composer.ComposerState(
                composer.ComposerDraft("test", "terminal", 1000),
                None,
            ),
            dialog=domain_modules.dialogs.DialogState(None),
            terminal=probe_models.TerminalSessionState(
                domain_ids.WindowId("window-one"),
                probe_models.TerminalInputState("test", None),
            ),
            errors=(),
        )


class FrameReader:
    """Read frames from one asynchronous stream."""

    def __init__(self, stream: AsyncGenerator[str, None]) -> None:
        """Store the source stream."""
        self._stream = stream

    async def next(self) -> str:
        """Return the next frame before the timeout.

        Returns:
            The next frame before the timeout.

        """
        return await asyncio.wait_for(anext(self._stream), 3)

    async def aclose(self) -> None:
        """Close the source stream."""
        await self._stream.aclose()

    async def asend(self) -> str:
        """Ask the source stream for its next frame.

        Returns:
            The next frame without a time limit.

        """
        return await self._stream.asend(None)


class BrokenReadModel:
    """Fail all session delta reads."""

    failure_prefix = "cannot read session"

    def delta(self, session_id: domain_ids.SessionId, cursor: int, entry_cursor: int | None = None) -> Never:
        """Fail a session delta read.

        Raises:
            RuntimeError: For each read.

        """
        message = f"{self.failure_prefix} {session_id} after cursor {cursor} and entry row {entry_cursor}"
        raise RuntimeError(message)
