# Copyright (c) 2026 Zhambyl Yermagambet
"""The kit runs a real worker's command and reads an empty record page through a private host (P08-T01)."""

from __future__ import annotations

from contextlib import ExitStack
from typing import TYPE_CHECKING

import pytest
from baqylau_extension_api.models.command_results import CommandSucceeded
from baqylau_extension_testkit.data_models import CommandRequest
from baqylau_extension_testkit.data_reads import command, records
from baqylau_extension_testkit.isolation import PrivateRoots
from baqylau_extension_testkit.lifecycle import change
from baqylau_extension_testkit.signoff import signoff

from tests.extension_host import test_terminal_action_daemon as action_daemon, testkit_host_fixture as hosts

if TYPE_CHECKING:
    from pathlib import Path

OWNER = action_daemon.OWNER
SCOPE = action_daemon.INSTALLATION.model_dump_json()
TEST_TIMEOUT_SECONDS = 180


@pytest.mark.timeout(TEST_TIMEOUT_SECONDS)
def test_command_job_and_records(tmp_path: Path, runtime_wheels: Path) -> None:
    """The command's job succeeds with the worker's reply; the host's work drains and signs off."""
    roots = PrivateRoots(tmp_path / "host")
    action_daemon.install(roots.root, runtime_wheels)
    with ExitStack() as cleanup:
        client = hosts.started(cleanup, roots).client
        assert change(client, OWNER, "enable", "kit-enable").status == "succeeded"
        request = CommandRequest(scope=SCOPE, request_key="kit-show", arguments='"readme"')
        job = command(client, OWNER, f"{OWNER}.show", request)
        assert isinstance(job.result, CommandSucceeded), job
        assert job.result.document.json_text == '"readme"'
        assert records(client, OWNER, f"{OWNER}.notes", SCOPE).records == ()
        assert signoff(client).raw_event_count == 0
