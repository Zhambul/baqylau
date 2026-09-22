# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep settings request identity stable through concurrency, failure, and restart."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

import pytest

from extensions.lifecycle_control_contract import LifecycleConflictError
from tests.extension_host import (
    lifecycle_control_fixture as controls,
    lifecycle_dependency_fixture as packages,
    settings_control_fixture as fixture,
)

CUSTOM = '"retry value"'


def test_settings_retry_before_and_after_commit(tmp_path: Path) -> None:
    """A retry does not construct another settings or runtime revision."""
    packages.write_settings_package(tmp_path)
    with closing(controls.open_control(tmp_path)) as case:
        selected = fixture.request(case, "retry", CUSTOM)
        admitted = fixture.service(case).change_settings(fixture.OWNER, selected)
        assert fixture.service(case).change_settings(fixture.OWNER, selected).operation == admitted.operation
        case.host.finish()
        assert fixture.service(case).change_settings(fixture.OWNER, selected).status == "replayed"
        assert fixture.snapshot(case).settings_revision == 1


def test_settings_retry_after_restart(tmp_path: Path) -> None:
    """Stored request bodies survive a new daemon owner and remain exact retries."""
    packages.write_settings_package(tmp_path)
    with closing(controls.open_control(tmp_path)) as case:
        selected = fixture.save(case, "restart", CUSTOM)
    with closing(controls.open_control(tmp_path)) as restarted:
        assert fixture.service(restarted).change_settings(fixture.OWNER, selected).status == "replayed"
        assert fixture.snapshot(restarted).settings_revision == 1
        assert fixture.snapshot(restarted).effective.json_text == CUSTOM


def test_settings_concurrent_retry_is_one_request(tmp_path: Path) -> None:
    """Two callers share one admitted operation and one saved revision."""
    packages.write_settings_package(tmp_path)
    with closing(controls.open_control(tmp_path)) as case, ThreadPoolExecutor(max_workers=2) as pool:
        selected = fixture.request(case, "concurrent", CUSTOM)
        pending = tuple(
            pool.submit(fixture.service(case).change_settings, fixture.OWNER, selected)
            for _ in range(2)
        )
        replies = tuple(task.result(timeout=5) for task in pending)
        assert {reply.status for reply in replies} == {"accepted", "replayed"}
        case.host.finish()
        assert fixture.snapshot(case).settings_revision == 1


def test_settings_key_cannot_be_lifecycle_key(tmp_path: Path) -> None:
    """Settings and enable operations share one durable user request namespace."""
    packages.write_settings_package(tmp_path)
    with closing(controls.open_control(tmp_path)) as case:
        fixture.save(case, "shared-key", CUSTOM)
        selected = case.request("enable", "shared-key")
        with pytest.raises(LifecycleConflictError, match="different extension lifecycle request"):
            case.control.change_lifecycle(fixture.OWNER, selected)
        assert not fixture.snapshot(case).selected_from_committed
