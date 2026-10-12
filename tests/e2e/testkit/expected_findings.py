# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep the audit rows that a scenario causes on purpose, so that the pipeline checks excuse only those rows."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from sdk.client import BaqylauClient
from tests.e2e.testkit import process

if TYPE_CHECKING:
    from collections.abc import Iterable

    from sdk import application_models


@dataclass
class ExpectedAuditErrors:
    """Name each expected row by its cursor, so that a later row of the same action is still a finding."""

    cursors: set[int] = field(default_factory=set)

    def expect(self, cursors: Iterable[int]) -> None:
        """Excuse these rows in the pipeline checks of the scenario and of the run."""
        self.cursors.update(cursors)


@dataclass(frozen=True)
class PipelineCheck:
    """Check the pipeline of the test application, and excuse the audit rows that scenarios expect."""

    client: BaqylauClient
    expected: ExpectedAuditErrors

    def assert_clean(
        self,
        label: str,
        start: application_models.models.DiagnosticsCheckpointResponse,
        end: application_models.models.DiagnosticsCheckpointResponse,
    ) -> None:
        """Check the diagnostics between two checkpoints."""
        report = self.client.diagnostics.report(start, end)
        process.assert_clean_diagnostics(label, report, self.expected.cursors)
