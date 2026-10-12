# Copyright (c) 2026 Zhambyl Yermagambet
"""Fail an accepted command job when the active package no longer declares commands.

The job stayed accepted, so the engine submitted it again after every batch of
facts, and each run failed again without a record.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, cast

from baqylau_extension_api.contracts.plugin import ExtensionCapabilities, ExtensionPlugin

from api.extensions import command_service
from domain.extension_jobs import JobState
from extensions import command_dispatch
from tests import command_job_fixture as jobs_fixture, command_package_fixture as packages
from tests.extension_host import registry_fixture

if TYPE_CHECKING:
    from baqylau_extension_api.contracts.lifecycle import ExtensionLifecycle

    from extensions.registry_package import RegistryPackage
    from tests import sqlite_repository_dependencies as repository_dependencies


def _without_commands(package: RegistryPackage) -> RegistryPackage:
    capabilities = ExtensionCapabilities(lifecycle=cast("ExtensionLifecycle", None))
    plugin = packages.FakePlugin(extension_info=package.entry.extension_info, capabilities=capabilities)
    return replace(package, plugin=cast("ExtensionPlugin", plugin))


def test_command_of_a_removed_capability_fails(main: repository_dependencies.SqliteDatabase) -> None:
    """The active package has no commands now, so the waiting job fails once."""
    dispatch = jobs_fixture.dispatch(main, packages.FakeCommands())
    accepted = command_service.accept_command(
        dispatch, packages.OWNER, packages.COMMAND_ID, packages.SCOPE, packages.a_request(),
    )
    without = replace(dispatch, packages=(_without_commands(dispatch.packages[0]),))

    job = command_dispatch.execute_command_job(without, accepted)

    assert job.state == JobState.FAILED
    assert job.diagnostic is not None
    assert command_dispatch.REMOVED_CODE in job.diagnostic


def test_command_of_an_inactive_package_waits(main: repository_dependencies.SqliteDatabase) -> None:
    """A package that is not active can return with its commands, so its job stays accepted."""
    dispatch = jobs_fixture.dispatch(main, packages.FakeCommands())
    accepted = command_service.accept_command(
        dispatch, packages.OWNER, packages.COMMAND_ID, packages.SCOPE, packages.a_request(),
    )
    waiting = replace(dispatch, packages=(registry_fixture.inactive(dispatch.packages[0]),))

    assert command_dispatch.execute_command_job(waiting, accepted).state == JobState.ACCEPTED
