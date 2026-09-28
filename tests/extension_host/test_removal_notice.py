# Copyright (c) 2026 Zhambyl Yermagambet
"""Tell an optional consumer which consumed service a runtime change removed (C16)."""

from contextlib import closing
from pathlib import Path

from baqylau_extension_api.models.lifecycle import RemovedService

from tests.extension_host import (
    control_assertions,
    lifecycle_control_fixture as controls,
    removal_notice_fixture as fixture,
)

REMOVED = RemovedService(owner=fixture.PROVIDER, name=f"{fixture.PROVIDER}.service")


def test_optional_consumer_receives_the_removal(tmp_path: Path, runtime_wheels: Path) -> None:
    """Disabling the provider keeps the consumer, and its new activation names the removed service."""
    fixture.write_peers(tmp_path, runtime_wheels)
    with closing(controls.open_control(tmp_path)) as case:
        for owner in (fixture.PROVIDER, fixture.CONSUMER):
            case.control.change_lifecycle(owner, case.request("enable", owner, owner))
            case.host.finish()
        assert not fixture.last_activation(tmp_path).removed_services

        case.control.change_lifecycle(fixture.PROVIDER, case.request("disable", "remove", fixture.PROVIDER))
        case.host.finish()

        control_assertions.require_enabled_owners(case, (fixture.CONSUMER,))
        assert fixture.last_activation(tmp_path).removed_services == (REMOVED,)
