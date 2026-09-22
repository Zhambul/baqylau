# Copyright (c) 2026 Zhambyl Yermagambet
"""Consume extension-only facts in the actual private application process."""

from pathlib import Path

from tests.extension_host import process_fixture, source_daemon_fixture as fixture

FACT_COUNT = 2


def test_daemon_consumes_extension_tail(tmp_path: Path, runtime_wheels: Path) -> None:
    """Source workers produce facts; core progress survives disable and restart."""
    case = fixture.installed(tmp_path, runtime_wheels, '"first"\n"second"\n')
    with process_fixture.running_catalog(tmp_path) as client:
        case.change(client, "enable")
        fixture.require_facts(case, ('"first"\n', '"second"\n'))
        fixture.require_core_progress(case, FACT_COUNT)
        before = fixture.journals(case)
        case.change(client, "disable")
    with process_fixture.running_catalog(tmp_path):
        fixture.require_core_progress(case, FACT_COUNT)
        assert fixture.journals(case) == before
        assert len(case.facts()) == FACT_COUNT
