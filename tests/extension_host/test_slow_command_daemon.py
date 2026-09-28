# Copyright (c) 2026 Zhambyl Yermagambet
"""A slow command runs while the same worker's transforms keep processing input (C11, P08-T03)."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from baqylau_extension_api.manifest.operations import CommandDefinition
from baqylau_extension_api.models.scopes import InstallationScope

from api.extensions.command_models import ExtensionCommandRequest
from tests import terminal_pty_waits
from tests.extension_api import ordered_transform_operations as operations, slow_command_example
from tests.extension_host import (
    ordered_processing_fixture as fixture,
    package_fixture,
    process_fixture,
    source_daemon_fixture as source,
)

if TYPE_CHECKING:
    from baqylau_extension_api.manifest.package import ExtensionManifest

    from sdk.client import BaqylauClient

OWNER = operations.FIRST_OWNER
COMMAND_ID = f"{OWNER}.slow"
SCOPE = InstallationScope()
JOB_SECONDS = 60
TEST_TIMEOUT_SECONDS = 180


def add_slow_command(directory: Path) -> None:
    """Give the first transform package the slow command in the same worker."""
    package = directory / "packages" / OWNER
    manifest = package_fixture.read_manifest(package)
    assert manifest.backend is not None
    package_fixture.save_manifest(package, manifest.model_copy(update={
        "backend": manifest.backend.model_copy(update={"module": "ordered_feature.slow_command_example"}),
        "capabilities": (*manifest.capabilities, "commands"),
        "contributions": manifest.contributions.model_copy(update={"commands": (slow_command(manifest),)}),
    }))
    text = Path(slow_command_example.__file__).read_text(encoding="utf-8")
    external = text.replace("from tests.extension_api import", "from ordered_feature import")
    package_fixture.write_file(package, "ordered_feature/slow_command_example.py", external.encode("utf-8"))


def slow_command(manifest: ExtensionManifest) -> CommandDefinition:
    """Declare the slow read command with the package's own text schema.

    Returns:
        The command declaration.

    """
    schema = manifest.schemas[0].reference
    return CommandDefinition(
        name=COMMAND_ID, scopes=("installation",), arguments=schema, result=schema, effect="read", reconciliation=False,
    )


def job_state(client: BaqylauClient, job_id: str) -> str:
    """Read the job's stored state.

    Returns:
        The state.

    """
    return str(client.extensions.jobs.read(OWNER, job_id, SCOPE).state)


@pytest.mark.timeout(TEST_TIMEOUT_SECONDS)
def test_transforms_run_during_a_slow_command(tmp_path: Path, runtime_wheels: Path) -> None:
    """Input is processed by both transform workers while the command is still running."""
    case = fixture.installed(tmp_path, runtime_wheels)
    add_slow_command(tmp_path)
    with process_fixture.running_catalog(tmp_path) as client:
        fixture.enable(client)
        request = ExtensionCommandRequest(scope=SCOPE.model_dump_json(), request_key="slow-1", arguments='"slow"')
        job = client.extensions.commands.submit(OWNER, COMMAND_ID, request)
        case.append('"during"\n')
        source.require_facts(case, fixture.expected("during"))
        assert job_state(client, job.job_id) == "running"
        terminal_pty_waits.wait_until(lambda: job_state(client, job.job_id) == "succeeded", JOB_SECONDS)
