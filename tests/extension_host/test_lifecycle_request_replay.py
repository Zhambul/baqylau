# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep one user request bound to its original operation across state changes."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from typing import Final

import pytest

from extensions.lifecycle_control_contract import LifecycleConflictError
from tests.extension_host import lifecycle_control_fixture as fixtures, package_fixture

OWNER = package_fixture.OWNER
ENABLE: Final = "enable"
PACKAGES = "packages"


def test_request_replays_before_and_after_commit(tmp_path: Path) -> None:
    """Retry does not select current settings or construct another runtime."""
    package_fixture.write_package(tmp_path / PACKAGES, web=True)
    with closing(fixtures.open_control(tmp_path)) as case:
        request = case.request(ENABLE, "stable")
        admitted = case.control.change_lifecycle(OWNER, request)
        assert case.control.change_lifecycle(OWNER, request).operation == admitted.operation
        case.host.finish()
        replayed = case.control.change_lifecycle(OWNER, request)
        assert replayed.status == "replayed" and replayed.operation is not None
        assert replayed.operation.status == "succeeded"
        assert case.host.controller.publish_ready().status == "idle"


def test_request_replays_after_restart(tmp_path: Path) -> None:
    """A different manager generation does not turn a retry into a new change."""
    package_fixture.write_package(tmp_path / PACKAGES, web=True)
    with closing(fixtures.open_control(tmp_path)) as case:
        request = case.request(ENABLE, "before-restart")
        case.control.change_lifecycle(OWNER, request)
        case.host.finish()
    with closing(fixtures.open_control(tmp_path)) as restarted:
        replayed = restarted.control.change_lifecycle(OWNER, request)
        assert replayed.status == "replayed"
        assert restarted.host.controller.publish_ready().status == "idle"


def test_key_cannot_select_another_request(tmp_path: Path) -> None:
    """Changes to revisions, owner, or body require a fresh request ID."""
    package_fixture.write_package(tmp_path / PACKAGES, web=True)
    with closing(fixtures.open_control(tmp_path)) as case:
        request = case.request(ENABLE, "bound")
        case.control.change_lifecycle(OWNER, request)
        case.host.finish()
        with pytest.raises(LifecycleConflictError, match="different extension lifecycle request"):
            case.control.change_lifecycle("test.other", request)
        with pytest.raises(LifecycleConflictError, match="different extension lifecycle request"):
            case.control.change_lifecycle(OWNER, request.model_copy(update={"expected_revision": 900}))


def test_concurrent_retry_has_one_operation(tmp_path: Path) -> None:
    """Concurrent clients cannot reserve two runtimes for the same complete request."""
    package_fixture.write_package(tmp_path / PACKAGES, web=True)
    with closing(fixtures.open_control(tmp_path)) as case, ThreadPoolExecutor(max_workers=2) as pool:
        request = case.request(ENABLE, "concurrent")
        futures = tuple(
            pool.submit(case.control.change_lifecycle, OWNER, request) for _ in range(2)
        )
        replies = tuple(pending.result(timeout=5) for pending in futures)
        assert {reply.status for reply in replies} == {"accepted", "replayed"}
        assert replies[0].operation == replies[1].operation
        case.host.finish()
