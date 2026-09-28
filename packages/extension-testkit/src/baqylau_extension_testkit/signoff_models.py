# Copyright (c) 2026 Zhambyl Yermagambet
"""Read the diagnostics replies of a test signoff; ignore fields that this kit does not use."""

from baqylau_extension_testkit.lifecycle_models import HostDocument


class CheckpointDocument(HostDocument):
    """Keep the raw event and audit cursors and the pending raw events."""

    raw_event_cursor: int
    audit_error_cursor: int
    pending_raw_event_count: int

    def describe(self) -> str:
        """Tell the pending raw events.

        Returns:
            One short line.

        """
        return f"{self.pending_raw_event_count} raw events pending"


class ExtensionWorkDocument(HostDocument):
    """Keep the owners with pending facts and the open jobs of the active packages."""

    projection_owners: tuple[str, ...]
    observer_owners: tuple[str, ...]
    open_jobs: int
    empty: bool

    def describe(self) -> str:
        """Tell the owners with pending facts and the open jobs.

        Returns:
            One short line.

        """
        projections = ", ".join(self.projection_owners)
        observers = ", ".join(self.observer_owners)
        return f"projections pending: [{projections}]; observers pending: [{observers}]; {self.open_jobs} open jobs"


class InterpretationProblemDocument(HostDocument):
    """Keep one raw event without a clean verdict."""

    raw_event_cursor: int
    source_type: str
    decision: str | None
    reason: str | None

    def describe(self) -> str:
        """Tell the raw event and its verdict.

        Returns:
            One short line.

        """
        cursor, source = self.raw_event_cursor, self.source_type
        return f"raw event {cursor} ({source}): {self.decision} {self.reason}"


class AuditProblemDocument(HostDocument):
    """Keep one recorded host error."""

    error_cursor: int
    component: str
    action: str

    def describe(self) -> str:
        """Tell the error and where it happened.

        Returns:
            One short line.

        """
        return f"host error {self.error_cursor}: {self.component} {self.action}"


class ReportDocument(HostDocument):
    """Keep the verdict count and every problem in a cursor range."""

    raw_event_count: int
    verdict_count: int
    interpretation_problems: tuple[InterpretationProblemDocument, ...]
    audit_problems: tuple[AuditProblemDocument, ...]

    def findings(self) -> tuple[str, ...]:
        """Tell each problem, and the raw events without a verdict.

        Returns:
            One line for each finding; none for a clean report.

        """
        interpretation = tuple(problem.describe() for problem in self.interpretation_problems)
        audit = tuple(problem.describe() for problem in self.audit_problems)
        found = (*interpretation, *audit)
        missing = self.raw_event_count - self.verdict_count
        return (*found, f"{missing} raw events have no verdict") if missing else found
