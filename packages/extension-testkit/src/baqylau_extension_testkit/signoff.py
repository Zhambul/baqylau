# Copyright (c) 2026 Zhambyl Yermagambet
"""Wait until the host has done all raw, canonical, projection, and job work, then check for problems."""

from __future__ import annotations

from dataclasses import dataclass

from baqylau_extension_testkit.client import HostClient
from baqylau_extension_testkit.signoff_models import CheckpointDocument, ExtensionWorkDocument, ReportDocument
from baqylau_extension_testkit.waiting import wait_until

SIGNOFF_SECONDS = 60.0
CHECKPOINT_PATH = "/api/diagnostics/checkpoint"
WORK_PATH = "/api/diagnostics/extension-work"
REPORT_PATH = "/api/diagnostics/report?through_raw_event=%d&through_audit_error=%d"


class SignoffError(AssertionError):
    """Report raw events without a clean verdict, or recorded host errors."""


def signoff(client: HostClient, seconds: float = SIGNOFF_SECONDS) -> ReportDocument:
    """Wait for drained work, then require a verdict for every raw event and no problems.

    Returns:
        The clean report.

    Raises:
        SignoffError: If a raw event has no verdict or a problem is recorded.

    """
    drain = _Drain(client)
    checkpoint = wait_until(drain.drained, seconds, drain.describe)
    path = REPORT_PATH % (checkpoint.raw_event_cursor, checkpoint.audit_error_cursor)
    report = client.read(path, ReportDocument)
    findings = report.findings()
    if findings:
        raise SignoffError("\n".join(findings))
    return report


@dataclass
class _Drain:
    client: HostClient
    state: tuple[str, ...] = ("the host was not read",)

    def drained(self) -> CheckpointDocument | None:
        checkpoint = self.client.read(CHECKPOINT_PATH, CheckpointDocument)
        work = self.client.read(WORK_PATH, ExtensionWorkDocument)
        self.state = (checkpoint.describe(), work.describe())
        if checkpoint.pending_raw_event_count or not work.empty:
            return None
        return checkpoint

    def describe(self) -> str:
        return "; ".join(self.state)
