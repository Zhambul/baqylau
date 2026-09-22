# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep one committed journal across an unclean daemon death."""

import time
from pathlib import Path

from tests import terminal_pty_waits
from tests.e2e.testkit.process import STOP_TIMEOUT_SECONDS
from tests.extension_host import (
    lifecycle_daemon_fixture as fixture,
    process_fixture,
)

DROP_BEHAVIOR = "drop"
SLOW_BEHAVIOR = "slow"
UNCHANGED_KINDS = (*fixture.REQUIRED_KINDS, "turn.finished")
CRASH_DELAY_SECONDS = 0.3


def test_crash_after_commit_keeps_one_journal(tmp_path: Path, runtime_wheels: Path) -> None:
    """A killed daemon keeps its committed journal and accepts no duplicate."""
    case = fixture.installed(tmp_path, runtime_wheels, DROP_BEHAVIOR)
    with process_fixture.running_application(tmp_path) as (client, process):
        case.change(client, fixture.ENABLE_ACTION)
        fixture.deliver_hook(client, fixture.hook(fixture.STOP_HOOK, fixture.STOP_IDENTITY))
        fixture.require_core_facts(case, fixture.REQUIRED_KINDS)
        before = case.journals()
        process.process.kill()
        process.process.join(STOP_TIMEOUT_SECONDS)
    assert len(before) == 1

    with process_fixture.running_catalog(tmp_path):
        assert case.journals() == before
        assert case.pending_count() == 0


def test_crash_before_commit_processes_once(tmp_path: Path, runtime_wheels: Path) -> None:
    """A killed daemon leaves the pending input for one complete retry."""
    case = fixture.installed(tmp_path, runtime_wheels, SLOW_BEHAVIOR)
    with process_fixture.running_application(tmp_path) as (client, process):
        case.change(client, fixture.ENABLE_ACTION)
        fixture.deliver_hook(client, fixture.hook(fixture.STOP_HOOK, fixture.STOP_IDENTITY))
        terminal_pty_waits.wait_until(lambda: bool(case.raw_event_ids()))
        time.sleep(CRASH_DELAY_SECONDS)
        assert not case.journals()
        process.process.kill()
        process.process.join(STOP_TIMEOUT_SECONDS)

    with process_fixture.running_catalog(tmp_path):
        fixture.require_core_facts(case, UNCHANGED_KINDS)
        assert len(case.journals()) == 1
        assert case.pending_count() == 0
