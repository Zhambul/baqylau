# Copyright (c) 2026 Zhambyl Yermagambet
"""Drain more than one raw page through the actual daemon and an external worker."""

from pathlib import Path

from extensions.processing_batch import INTERPRETATION_BATCH_SIZE
from tests.extension_host import process_fixture, source_daemon_fixture as fixture

ORIGINAL_COUNT = INTERPRETATION_BATCH_SIZE + 1


def test_daemon_finishes_raw_page_continuation(tmp_path: Path, runtime_wheels: Path) -> None:
    """A retained file-only source needs no new file change to complete its raw tail."""
    expected = tuple(f'"line-{index}"\n' for index in range(ORIGINAL_COUNT))
    case = fixture.installed(tmp_path, runtime_wheels, "".join(expected))
    with process_fixture.running_catalog(tmp_path) as client:
        case.change(client, "enable")
        fixture.require_facts(case, expected)
        fixture.require_core_progress(case, ORIGINAL_COUNT)
        before = fixture.journals(case)
        assert len(before) == ORIGINAL_COUNT
        case.change(client, "disable")
    with process_fixture.running_catalog(tmp_path):
        fixture.require_core_progress(case, ORIGINAL_COUNT)
        assert fixture.journals(case) == before
