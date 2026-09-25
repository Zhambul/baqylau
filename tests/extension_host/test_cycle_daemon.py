# Copyright (c) 2026 Zhambyl Yermagambet
"""An order cycle is refused with its reason, and unrelated work continues (C17, P08-T03)."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from baqylau_extension_api.manifest.metadata import LoadOrder

from tests.extension_api import operation_samples
from tests.extension_host import (
    lifecycle_http_fixture as lifecycle,
    package_fixture,
    process_fixture,
    source_daemon_fixture as source,
)

if TYPE_CHECKING:
    from pathlib import Path

FIRST, SECOND = "test.cycle-a", "test.cycle-b"
TEST_TIMEOUT_SECONDS = 180


def write_cycle(directory: Path) -> None:
    """Write two web packages; each must load before the other."""
    for owner, other in ((FIRST, SECOND), (SECOND, FIRST)):
        package = package_fixture.write_package(directory / "packages", owner, web=True)
        manifest = package_fixture.read_manifest(package)
        order = LoadOrder(before=(other,))
        package_fixture.save_manifest(package, manifest.model_copy(update={"load_order": order}))


@pytest.mark.timeout(TEST_TIMEOUT_SECONDS)
def test_cycle_is_refused_and_work_continues(tmp_path: Path, runtime_wheels: Path) -> None:
    """The second package of the cycle is refused by name of the rule; the source keeps processing input."""
    case = source.installed(tmp_path, runtime_wheels)
    write_cycle(tmp_path)
    with process_fixture.running_catalog(tmp_path) as client:
        case.change(client, "enable")
        request = lifecycle.lifecycle_request(client, FIRST, "enable", "enable-first")
        admitted = client.extensions.lifecycle.change(FIRST, request)
        assert lifecycle.wait_operation(client, admitted.operation.operation_id).status == "succeeded"
        refused = client.transport.client.post(
            f"/api/extensions/{SECOND}/lifecycle",
            content=lifecycle.lifecycle_request(client, SECOND, "enable", "enable-second").model_dump_json(),
            headers={"Content-Type": "application/json"},
        )
        assert refused.status_code == HTTPStatus.BAD_REQUEST
        assert "cycle" in refused.json()["error"]
        case.append('"after"\n')
        source.require_facts(case, ('"first"\n', '"after"\n'))
        assert {FIRST, operation_samples.OWNER} <= lifecycle.active_owners(client)
