# Copyright (c) 2026 Zhambyl Yermagambet
"""Count the outcomes of a package's declared cases; live skips are counted apart."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

LIVE_MARKER = "baqylau_live"


@dataclass
class CaseOutcomes:
    """Skip live cases unless they are selected, and count each outcome once."""

    live: bool
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    skipped_live: int = 0
    live_ids: set[str] = field(default_factory=set)

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        """Skip a live case unless live cases are selected; pytest reads skip marks at case setup."""
        skip = pytest.mark.skip(reason="a live case; run with --live")
        for case_item in session.items:
            if case_item.get_closest_marker(LIVE_MARKER) is not None:
                self.live_ids.add(case_item.nodeid)
                if not self.live:
                    case_item.add_marker(skip)

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        """Count one case's outcome once."""
        if report.failed:
            self.failed += 1
        elif report.skipped:
            self._skip(report.nodeid)
        elif report.when == "call":
            self.passed += 1

    def describe(self) -> str:
        """Tell the repeatable outcomes and the live skips apart.

        Returns:
            One line.

        """
        passed, failed, skipped = self.passed, self.failed, self.skipped
        return f"repeatable passed {passed}, failed {failed}, skipped {skipped}; live skipped {self.skipped_live}"

    def _skip(self, nodeid: str) -> None:
        if nodeid in self.live_ids:
            self.skipped_live += 1
        else:
            self.skipped += 1
