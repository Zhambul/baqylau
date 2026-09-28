# Copyright (c) 2026 Zhambyl Yermagambet
"""Check a package's E2E coverage, then run its declared cases.

    python -m baqylau_extension_testkit.runner PACKAGE_DIRECTORY [--live]

The harness list comes from a private host that the installed executable
starts. A case marked `baqylau_live` needs a real terminal or harness; without
`--live` it is skipped and reported apart from the repeatable cases.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from baqylau_extension_testkit.case_outcomes import CaseOutcomes
from baqylau_extension_testkit.coverage import coverage_findings, read_manifest
from baqylau_extension_testkit.harness_list import discovered_harnesses
from baqylau_extension_testkit.host_process import HostExecutable

if TYPE_CHECKING:
    from collections.abc import Sequence

LIVE_OPTION = "--live"
COVERAGE_FAILED = 3


@dataclass(frozen=True)
class RunReport:
    """Keep the coverage findings and the outcomes of the cases that ran."""

    findings: tuple[str, ...]
    outcomes: CaseOutcomes


def run(package: Path, harnesses: frozenset[str], *, live: bool = False) -> RunReport:
    """Check coverage; run the declared cases only when coverage is complete.

    Returns:
        The coverage findings and the case outcomes.

    """
    manifest = read_manifest(package)
    outcomes = CaseOutcomes(live=live)
    findings = coverage_findings(package, manifest, harnesses)
    if not findings:
        paths = [str(package / case.path) for case in manifest.e2e]
        options = ["-q", "-p", "no:cacheprovider", "--rootdir", str(package)]
        pytest.main([*options, *paths], plugins=[outcomes])
    return RunReport(findings, outcomes)


def main(arguments: Sequence[str]) -> int:
    """Run the command.

    Returns:
        0 when coverage is complete and no case failed.

    """
    package = Path(next(argument for argument in arguments if argument != LIVE_OPTION))
    harnesses = discovered_harnesses(HostExecutable.from_environment())
    report = run(package, harnesses, live=LIVE_OPTION in arguments)
    for finding in report.findings:
        sys.stdout.write(f"coverage: {finding}\n")
    summary = report.outcomes.describe()
    sys.stdout.write(f"{summary}\n")
    if report.findings:
        return COVERAGE_FAILED
    return int(bool(report.outcomes.failed))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
