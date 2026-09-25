# Copyright (c) 2026 Zhambyl Yermagambet
"""A transform package reloads while source input arrives: no input is lost and each uses one runtime (C03)."""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

import pytest

from extensions.models import interpretation_steps as steps
from tests import terminal_pty_waits
from tests.extension_api import ordered_transform_operations as operations
from tests.extension_host import (
    lifecycle_http_fixture as lifecycle,
    ordered_processing_fixture as fixture,
    process_fixture,
    source_daemon_fixture as source,
)

if TYPE_CHECKING:
    from pathlib import Path

    from extensions.models.interpretations import InterpretationCommit
    from sdk.client import BaqylauClient

AFTER_RELOAD = 5
APPEND_SECONDS = 0.1
WAIT_SECONDS = 120
TEST_TIMEOUT_SECONDS = 240
PASSED_STAGES = frozenset(("applied", "core_lifecycle", "core_activity"))
RUNTIMES = ("before the reload", "after the reload")
EXTENSION_STEPS = (steps.RawTransformStep, steps.ExtensionTranslationStep, steps.CanonicalTransformStep)


def runtimes(commit: InterpretationCommit) -> frozenset[str]:
    """Read the runtime revisions that one input's worker calls used.

    Returns:
        The revisions.

    """
    return frozenset(
        step.request.context.runtime_revision for step in commit.proposal.steps if isinstance(step, EXTENSION_STEPS)
    )


class Writer:
    """Append numbered inputs until the reload ends, then a few more."""

    def __init__(self, case: source.SourceDaemon) -> None:
        """Start with no input."""
        self.case = case
        self.reloaded = threading.Event()
        self.lines: list[str] = []
        self.thread = threading.Thread(target=self.write)

    def finish(self) -> tuple[str, ...]:
        """Write the last inputs after the reload and wait for the writer.

        Returns:
            Every appended input.

        """
        self.reloaded.set()
        self.thread.join()
        return tuple(self.lines)

    def processed(self) -> bool:
        """Tell if every appended input has its stored interpretation.

        Returns:
            True when each input has one journal.

        """
        return len(source.journals(self.case)) == len(self.lines)

    def write(self) -> None:
        """Append one input every step."""
        remaining = AFTER_RELOAD
        while remaining:
            line = f'"input-{len(self.lines)}"\n'
            self.case.append(line)
            self.lines.append(line)
            time.sleep(APPEND_SECONDS)
            if self.reloaded.is_set():
                remaining -= 1


def new_version(directory: Path, client: BaqylauClient) -> None:
    """Change the first transform package's code and rescan, as a real package update does."""
    changed = directory / "packages" / operations.FIRST_OWNER / "ordered_feature" / "ordered_raw_example.py"
    with changed.open("a", encoding="utf-8") as stream:
        stream.write("# A new package version.\n")
    client.extensions.rescan(client.extensions.catalog().revision)


def reload_first(client: BaqylauClient) -> None:
    """Reload the first transform package and wait for its operation."""
    request = lifecycle.lifecycle_request(client, operations.FIRST_OWNER, "reload", "reload-under-input")
    admitted = client.extensions.lifecycle.change(operations.FIRST_OWNER, request)
    assert lifecycle.wait_operation(client, admitted.operation.operation_id).status == "succeeded"


def runtime_order(case: source.SourceDaemon) -> list[str]:
    """Require one runtime and only applied worker steps for each input.

    Returns:
        Each input's runtime revision, in input order.

    """
    journals = source.journals(case)
    used = [runtimes(journal) for journal in journals]
    assert all(len(revisions) == 1 for revisions in used)
    assert all(set(fixture.outcomes(journal)) <= PASSED_STAGES for journal in journals)
    return [next(iter(revisions)) for revisions in used]


@pytest.mark.timeout(TEST_TIMEOUT_SECONDS)
def test_reload_while_input_arrives(tmp_path: Path, runtime_wheels: Path) -> None:
    """Every input is stored and processed once, each by one runtime, and the runtime changes once in order."""
    case = fixture.installed(tmp_path, runtime_wheels)
    with process_fixture.running_catalog(tmp_path) as client:
        fixture.enable(client)
        writer = Writer(case)
        writer.thread.start()
        new_version(tmp_path, client)
        reload_first(client)
        expected = writer.finish()
        terminal_pty_waits.wait_until(writer.processed, WAIT_SECONDS)
        assert case.texts() == expected
        order = runtime_order(case)
        assert len(set(order)) == len(RUNTIMES)
        assert order == sorted(order, key=order.index)
