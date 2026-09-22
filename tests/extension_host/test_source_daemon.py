# Copyright (c) 2026 Zhambyl Yermagambet
"""Check source ingestion through private application and backend processes."""

from pathlib import Path
from threading import Event

from tests.extension_host import process_fixture, source_daemon_fixture as fixture

FIRST = '"first"\n'
PARTIAL = '"partial"\n'
SECOND = '"second"\n'
THIRD = '"third"\n'
FIRST_POSITION = str(len(FIRST.encode()))
DISABLED_WAIT_SECONDS = 0.2


def test_daemon_reads_partial_and_replaced_files(tmp_path: Path, runtime_wheels: Path) -> None:
    """The real engine commits complete lines and detects an atomic file replacement."""
    case = fixture.installed(tmp_path, runtime_wheels, f'{FIRST}"partial')
    with process_fixture.running_catalog(tmp_path) as client:
        case.change(client, "enable")
        case.require_text((FIRST,))
        assert case.originals()[0].source_position == FIRST_POSITION
        case.append('"\n')
        case.require_text((FIRST, PARTIAL))
        previous = case.originals()[0].candidate.source_identity
        replacement = tmp_path / "replacement.log"
        replacement.write_text(SECOND, encoding="utf-8")
        replacement.replace(case.journal)
        case.require_text((FIRST, PARTIAL, SECOND))
        assert case.originals()[-1].candidate.source_identity != previous
        fixture.require_facts(case, (FIRST, PARTIAL, SECOND))


def test_daemon_resumes_and_disables_source(tmp_path: Path, runtime_wheels: Path) -> None:
    """Restart uses retained bytes and progress; disable stops future source writes."""
    case = fixture.installed(tmp_path, runtime_wheels)
    with process_fixture.running_catalog(tmp_path) as client:
        case.change(client, "enable")
        case.require_text((FIRST,))
        fixture.require_facts(case, (FIRST,))
    case.package.rename(tmp_path / "removed-package")
    case.append(SECOND)
    with process_fixture.running_catalog(tmp_path) as client:
        fixture.require_facts(case, (FIRST, SECOND))
        runtime = client.extensions.lifecycle.state().active_runtime
        assert runtime is not None
        reads = case.reads(runtime)
        assert any(read.request.after_position == FIRST_POSITION for read in reads)
        case.change(client, "disable")
        case.append(THIRD)
        assert not Event().wait(DISABLED_WAIT_SECONDS)
        assert case.texts() == (FIRST, SECOND)
    with process_fixture.running_catalog(tmp_path):
        assert case.texts() == (FIRST, SECOND)
