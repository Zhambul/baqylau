# Copyright (c) 2026 Zhambyl Yermagambet
"""Settle an accepted observer job when the active package no longer declares the observer.

A package update removed its observer while jobs of that observer waited. The
jobs stayed accepted, so the engine submitted them again after every batch of
facts, and each attempt read the complete manager state twice. The idle host
used one full core for that.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, cast

from baqylau_extension_api.contracts.plugin import ExtensionCapabilities, ExtensionPlugin

from domain.extension_jobs import JobState
from extensions.observer_execution import REMOVED_CODE
from tests import command_package_fixture as packages, observer_pass_fixture
from tests.extension_host import registry_fixture

if TYPE_CHECKING:
    from pathlib import Path

    from baqylau_extension_api.contracts.lifecycle import ExtensionLifecycle

    from extensions.registry_package import RegistryPackage


def _without_observer(package: RegistryPackage) -> RegistryPackage:
    capabilities = ExtensionCapabilities(lifecycle=cast("ExtensionLifecycle", None))
    plugin = packages.FakePlugin(extension_info=package.entry.extension_info, capabilities=capabilities)
    return replace(package, plugin=cast("ExtensionPlugin", plugin))


def test_job_of_a_removed_observer_fails(tmp_path: Path) -> None:
    """The active package has no observer now, so the waiting job fails once and is not run again."""
    case = observer_pass_fixture.a_case(tmp_path)
    case.accept()

    case.execute(_without_observer(case.package))

    job = case.job()
    assert job.state == JobState.FAILED
    assert job.diagnostic is not None
    assert REMOVED_CODE in job.diagnostic
    assert not case.observer.requests


def test_job_of_an_inactive_package_waits(tmp_path: Path) -> None:
    """A package that is not active can return with its observer, so its job stays accepted."""
    case = observer_pass_fixture.a_case(tmp_path)
    case.accept()

    case.execute(registry_fixture.inactive(case.package))

    assert case.job().state == JobState.ACCEPTED
